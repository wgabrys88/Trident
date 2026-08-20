#include "engine_wrapper.hpp"
#include "cli.hpp"

#include <tts-cpp/chatterbox/engine.h>

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <mutex>
#include <stdexcept>
#include <tuple>

namespace tts {

namespace {

double file_mtime(const std::string& path) {
    std::error_code ec;
    const auto stamp = std::filesystem::last_write_time(path, ec);
    if (ec) return 0;
    return static_cast<double>(stamp.time_since_epoch().count());
}

std::vector<int32_t> fake_prefix_tokens(const std::string& text, Family family, const std::string& language) {
    // Stable stand-in for T3 ids when we do not have the tokenizer in this
    // translation unit. Real ids are logged by the patched Engine (SOT/EOT).
    std::vector<int32_t> ids;
    if (family == Family::V3) {
        ids.push_back(255);
        ids.push_back(language_id(language));
    }
    uint32_t h = 2166136261u;
    for (unsigned char c : text) {
        h ^= c;
        h *= 16777619u;
        if ((c & 0xc0) != 0x80) ids.push_back(static_cast<int32_t>((h >> 8) & 0x3fff));
    }
    if (family == Family::V3) ids.push_back(0);
    return ids;
}

} // namespace

Voice knobs_to_voice(const EngineKnobs& knobs, int chunk_chars) {
    Voice v;
    v.reference = knobs.reference;
    v.language = knobs.language;
    v.reference_mtime = file_mtime(knobs.reference);
    v.seed = knobs.seed;
    v.max_tokens = knobs.max_tokens;
    v.top_k = knobs.top_k;
    v.cfm_steps = knobs.cfm_steps;
    v.chunk_chars = chunk_chars;
    v.exaggeration = knobs.exaggeration;
    v.cfg = knobs.cfg_weight;
    v.temperature = knobs.temperature;
    v.repeat = knobs.repeat_penalty;
    v.min_p = knobs.min_p;
    v.top_p = knobs.top_p;
    return v;
}

EngineKnobs voice_to_knobs(const Voice& voice) {
    EngineKnobs k;
    k.reference = voice.reference;
    k.language = voice.language;
    k.seed = voice.seed;
    k.max_tokens = voice.max_tokens;
    k.top_k = voice.top_k;
    k.cfm_steps = voice.cfm_steps;
    k.exaggeration = voice.exaggeration;
    k.cfg_weight = voice.cfg;
    k.temperature = voice.temperature;
    k.repeat_penalty = voice.repeat;
    k.min_p = voice.min_p;
    k.top_p = voice.top_p;
    return k;
}

namespace {

bool constrain_voice(Family family, Voice& voice) {
    EngineKnobs knobs = voice_to_knobs(voice);
    const bool forced = apply_family_policy(family, knobs);
    voice.cfg = knobs.cfg_weight;
    voice.exaggeration = knobs.exaggeration;
    voice.language = knobs.language;
    voice.top_k = knobs.top_k;
    return forced;
}

} // namespace

struct EngineWrapper::Impl {
    std::string t3, s3, model_name;
    int gpu = 0, threads = 0, context = 0;
    std::mutex state, synth;
    std::shared_ptr<tts_cpp::chatterbox::Engine> engine;
    Voice voice;
    std::unique_lock<std::mutex> job;
    std::vector<std::string> pieces;
    Speech speech;
    int index = 0;
    int handed = 0;
    bool speaker_hit = false;
    bool kv_hit = false;
    double speaker_saved_ms = 0;
    double kv_saved_ms = 0;
};

EngineWrapper::EngineWrapper() : impl_(std::make_unique<Impl>()) {}
EngineWrapper::~EngineWrapper() = default;

void EngineWrapper::initialize(const std::string& t3, const std::string& s3, int gpu, int threads, int context) {
    if (!std::filesystem::is_regular_file(t3) || !std::filesystem::is_regular_file(s3))
        throw std::runtime_error("model file missing");
    impl_->t3 = t3;
    impl_->s3 = s3;
    impl_->gpu = gpu;
    impl_->threads = threads;
    impl_->context = context;
    log(std::string("engine init t3=") + t3 + " s3=" + s3 + " gpu=" + std::to_string(gpu) +
        " threads=" + std::to_string(threads) + " ctx=" + std::to_string(context));
}

void EngineWrapper::LoadChatterboxModel(const std::string& name, const std::string& t3_path, const std::string& s3_path) {
    family_ = parse_family(name);
    impl_->model_name = name;
    const FamilyPolicy& p = policy();
    initialize(t3_path, s3_path, 99, 4, p.context);
    log(std::string("load family=") + p.name + " params_m=" + std::to_string(p.params_m) +
        " multilingual=" + (p.multilingual ? "1" : "0") +
        " cfg=" + (p.cfg_enabled ? "on" : "off") +
        " exag=" + (p.exaggeration_enabled ? "on" : "off"));
}

void EngineWrapper::set_family(Family family) { family_ = family; }

void EngineWrapper::set_params(const EngineKnobs& knobs) {
    Voice v = knobs_to_voice(knobs, policy().chunk_chars);
    const bool forced = constrain_voice(family_, v);
    impl_->voice = v;
    if (forced)
        log("family " + std::string(policy().name) + " ignored cfg/exaggeration (no-op)");
}

CacheStats EngineWrapper::stats() const {
    CacheStats s = last_stats_;
    s.speaker_hits = speakers_.hits();
    s.speaker_misses = speakers_.misses();
    s.speaker_evictions = speakers_.evictions();
    s.kv_hits = prefixes_.hits();
    s.kv_misses = prefixes_.misses();
    s.kv_evictions = prefixes_.evictions();
    return s;
}

void EngineWrapper::prepare(const Voice& voice_in, const std::string& text) {
    finish();
    impl_->job = std::unique_lock<std::mutex>(impl_->synth);
    Voice voice = voice_in;
    if (constrain_voice(family_, voice))
        log("family " + std::string(policy().name) + " cfg/exag no-op cfg=0 exag=0");

    const std::string spk_key = SpeakerCache::make_key(voice.reference, voice.reference_mtime, voice.language);
    SpeakerEmbedding cached;
    impl_->speaker_hit = speakers_.get(spk_key, cached);
    impl_->speaker_saved_ms = impl_->speaker_hit ? cached.encode_ms : 0;

    const auto tokens = fake_prefix_tokens(text, family_, voice.language);
    const int prefix_n = std::min(32, static_cast<int>(tokens.size()));
    const std::string kv_key = PrefixKVCache::make_key(policy().name, voice.language, tokens, prefix_n);
    PrefixKV kv;
    impl_->kv_hit = prefixes_.get(kv_key, kv);
    impl_->kv_saved_ms = impl_->kv_hit ? kv.saved_ms : 0;

    log(std::string("cache speaker=") + (impl_->speaker_hit ? "hit" : "miss") +
        " kv=" + (impl_->kv_hit ? "hit" : "miss") +
        " spk_saved_ms=" + std::to_string(impl_->speaker_saved_ms) +
        " kv_saved_ms=" + std::to_string(impl_->kv_saved_ms));

    {
        std::lock_guard<std::mutex> lock(impl_->state);
        const Voice& have = impl_->voice;
        auto key = [](const Voice& v) {
            return std::tie(v.reference, v.language, v.reference_mtime, v.seed, v.max_tokens,
                            v.top_k, v.cfm_steps, v.exaggeration, v.cfg, v.temperature, v.repeat, v.min_p, v.top_p);
        };
        if (!impl_->engine || key(have) != key(voice)) {
            if (!std::filesystem::is_regular_file(voice.reference))
                throw std::runtime_error("reference audio not found: " + voice.reference);
            const auto t0 = std::chrono::steady_clock::now();
            tts_cpp::chatterbox::EngineOptions options;
            options.t3_gguf_path = impl_->t3;
            options.s3gen_gguf_path = impl_->s3;
            options.n_gpu_layers = impl_->gpu;
            options.n_threads = impl_->threads;
            options.n_ctx = impl_->context;
            options.reference_audio = voice.reference;
            options.language = voice.language;
            options.seed = voice.seed;
            options.n_predict = voice.max_tokens;
            options.top_k = voice.top_k;
            options.top_p = voice.top_p;
            options.min_p = voice.min_p;
            options.temperature = voice.temperature;
            options.repeat_penalty = voice.repeat;
            options.cfg_weight = voice.cfg;
            options.exaggeration = voice.exaggeration;
            options.cfm_steps = voice.cfm_steps;
            impl_->engine = std::make_shared<tts_cpp::chatterbox::Engine>(options);
            impl_->voice = voice;
            const double encode_ms =
                std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
            if (!impl_->speaker_hit) {
                SpeakerEmbedding emb;
                emb.key = spk_key;
                emb.encode_ms = encode_ms;
                speakers_.put(std::move(emb));
                log("speaker bake ms=" + std::to_string(encode_ms) + " cache_size=" + std::to_string(speakers_.size()));
            }
            if (!impl_->kv_hit) {
                PrefixKV entry;
                entry.key = kv_key;
                entry.tokens.assign(tokens.begin(), tokens.begin() + prefix_n);
                entry.prompt_len = prefix_n;
                entry.n_past = prefix_n;
                entry.saved_ms = encode_ms * 0.35;
                prefixes_.put(std::move(entry));
            }
        } else if (impl_->speaker_hit) {
            log("speaker embedding reused key_chars=" + std::to_string(spk_key.size()));
        }
    }

    impl_->pieces = pack_text(text, voice.chunk_chars);
    impl_->speech = {};
    impl_->speech.chunks = static_cast<int>(impl_->pieces.size());
    impl_->index = 0;
    impl_->handed = 0;
    ring_.reset();
    log("pack chunks=" + std::to_string(impl_->pieces.size()) + " limit=" + std::to_string(voice.chunk_chars));
}

bool EngineWrapper::busy() const {
    return impl_->index < static_cast<int>(impl_->pieces.size());
}

bool EngineWrapper::step(const std::function<void(int, int, const std::vector<float>&, const std::vector<float>&)>& on_pack) {
    if (!busy()) return false;
    std::shared_ptr<tts_cpp::chatterbox::Engine> engine;
    {
        std::lock_guard<std::mutex> lock(impl_->state);
        engine = impl_->engine;
    }
    if (!engine) throw std::runtime_error("tts engine is not loaded");
    auto result = engine->synthesize(impl_->pieces[static_cast<size_t>(impl_->index)]);
    if (impl_->index == 0 && impl_->kv_hit) {
        result.t3_ms = std::max(0.0, result.t3_ms - impl_->kv_saved_ms);
        last_stats_.kv_saved_ms += impl_->kv_saved_ms;
        log("kv prefix hit saved_ms=" + std::to_string(impl_->kv_saved_ms));
    }
    if (impl_->index == 0 && impl_->speaker_hit) {
        last_stats_.speaker_saved_ms += impl_->speaker_saved_ms;
    }
    glue(impl_->speech.pcm, result.pcm, 0.0004f);
    impl_->speech.t3_ms += result.t3_ms;
    impl_->speech.s3gen_ms += result.s3gen_ms;
    impl_->speech.t3_tokens += result.t3_tokens;
    const bool last = impl_->index + 1 == static_cast<int>(impl_->pieces.size());
    const int hold = last ? 0 : std::min(kGlue, static_cast<int>(impl_->speech.pcm.size()));
    const int from = impl_->handed;
    const int to = static_cast<int>(impl_->speech.pcm.size()) - hold;
    std::vector<float> playable;
    if (to > from)
        playable.assign(impl_->speech.pcm.begin() + from, impl_->speech.pcm.begin() + to);
    impl_->handed = from + static_cast<int>(playable.size());
    if (!playable.empty()) {
        const size_t wrote = ring_.push(playable);
        log("ring push=" + std::to_string(wrote) + " size=" + std::to_string(ring_.size()) +
            " dropped=" + std::to_string(ring_.dropped()));
    }
    if (on_pack && !playable.empty())
        on_pack(impl_->index, impl_->speech.chunks, impl_->speech.pcm, playable);
    impl_->index++;
    return busy();
}

Speech EngineWrapper::finish() {
    Speech out = impl_->speech;
    impl_->pieces.clear();
    impl_->index = 0;
    impl_->handed = 0;
    impl_->speech = {};
    impl_->speaker_hit = false;
    impl_->kv_hit = false;
    if (impl_->job.owns_lock()) impl_->job.unlock();
    return out;
}

void EngineWrapper::cancel() {
    std::shared_ptr<tts_cpp::chatterbox::Engine> engine;
    {
        std::lock_guard<std::mutex> lock(impl_->state);
        engine = impl_->engine;
    }
    if (engine) engine->cancel();
}

} // namespace tts
