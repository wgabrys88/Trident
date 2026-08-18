#include "engine_wrapper.hpp"
#include <tts-cpp/chatterbox/engine.h>
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <memory>
#include <mutex>
#include <stdexcept>

namespace tts {

struct EngineWrapper::Impl {
    std::string t3, s3;
    int gpu = 0, threads = 0, context = 0;
    std::mutex state, synth;
    std::shared_ptr<tts_cpp::chatterbox::Engine> engine;
    Voice voice;
    std::unique_lock<std::mutex> job;
    std::vector<std::string> pieces;
    Speech speech;
    int index = 0;
    int handed = 0;
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
}

static std::vector<std::string> pack_text(const std::string& text, int limit) {
    if (limit < 40) limit = 40;
    std::vector<std::string> bits;
    std::string cur;
    auto flush = [&] {
        while (!cur.empty() && cur.back() == ' ') cur.pop_back();
        if (!cur.empty()) bits.push_back(cur);
        cur.clear();
    };
    for (size_t i = 0; i < text.size(); ++i) {
        cur.push_back(text[i]);
        const unsigned char c = static_cast<unsigned char>(text[i]);
        if ((c == '.' || c == '!' || c == '?' || c == ';') &&
            (i + 1 == text.size() || text[i + 1] == ' ' || text[i + 1] == '\n'))
            flush();
    }
    flush();
    if (bits.empty()) return {text};
    std::vector<std::string> packed;
    std::string buf = bits[0];
    for (size_t i = 1; i < bits.size(); ++i) {
        if (static_cast<int>(buf.size() + 1 + bits[i].size()) > limit) {
            packed.push_back(buf);
            buf = bits[i];
        } else {
            buf += " " + bits[i];
        }
    }
    packed.push_back(buf);
    return packed;
}

static void glue(std::vector<float>& dst, const std::vector<float>& src) {
    if (dst.empty()) { dst = src; return; }
    if (src.empty()) return;
    const int n = std::min(kGlue, static_cast<int>(std::min(dst.size(), src.size())));
    const float step = 1.5707963267948966f / static_cast<float>(std::max(n, 1));
    for (int i = 0; i < n; ++i) {
        const float w = static_cast<float>(i) * step;
        dst[dst.size() - n + i] = dst[dst.size() - n + i] * std::cos(w) + src[i] * std::sin(w);
    }
    dst.insert(dst.end(), src.begin() + n, src.end());
}

void EngineWrapper::prepare(const Voice& voice, const std::string& text) {
    finish();
    impl_->job = std::unique_lock<std::mutex>(impl_->synth);
    {
        std::lock_guard<std::mutex> lock(impl_->state);
        const Voice& have = impl_->voice;
        const bool same = impl_->engine
            && have.reference == voice.reference
            && have.language == voice.language
            && have.reference_mtime == voice.reference_mtime
            && have.seed == voice.seed
            && have.max_tokens == voice.max_tokens
            && have.top_k == voice.top_k
            && have.cfm_steps == voice.cfm_steps
            && have.exaggeration == voice.exaggeration
            && have.cfg == voice.cfg
            && have.temperature == voice.temperature
            && have.repeat == voice.repeat
            && have.min_p == voice.min_p
            && have.top_p == voice.top_p;
        if (!same) {
            if (!std::filesystem::is_regular_file(voice.reference))
                throw std::runtime_error("reference audio not found: " + voice.reference);
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
        }
    }
    impl_->pieces = pack_text(text, voice.chunk_chars);
    impl_->speech = {};
    impl_->speech.chunks = static_cast<int>(impl_->pieces.size());
    impl_->index = 0;
    impl_->handed = 0;
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
    auto result = engine->synthesize(impl_->pieces[impl_->index]);
    glue(impl_->speech.pcm, result.pcm);
    impl_->speech.t3_ms += result.t3_ms;
    impl_->speech.s3gen_ms += result.s3gen_ms;
    const bool last = impl_->index + 1 == static_cast<int>(impl_->pieces.size());
    const int hold = last ? 0 : std::min(kGlue, static_cast<int>(impl_->speech.pcm.size()));
    const int from = impl_->handed;
    const int to = static_cast<int>(impl_->speech.pcm.size()) - hold;
    std::vector<float> playable;
    if (to > from) playable.assign(impl_->speech.pcm.begin() + from, impl_->speech.pcm.begin() + to);
    else if (last && static_cast<int>(impl_->speech.pcm.size()) > from)
        playable.assign(impl_->speech.pcm.begin() + from, impl_->speech.pcm.end());
    impl_->handed = from + static_cast<int>(playable.size());
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

}
