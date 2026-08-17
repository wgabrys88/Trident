#include "engine_wrapper.hpp"
#include <tts-cpp/chatterbox/engine.h>
#include <filesystem>
#include <mutex>
#include <random>
#include <stdexcept>
#include <unordered_map>
namespace tts {
struct EngineWrapper::Impl {
    struct Session {
        std::unique_ptr<tts_cpp::chatterbox::Engine> engine;
        std::mutex synthesis;
    };
    std::unordered_map<std::string, std::shared_ptr<Session>> sessions;
    mutable std::mutex mutex;
    std::string t3, s3;
    int gpu, threads, context, limit;
};
EngineWrapper::EngineWrapper() : impl_(std::make_unique<Impl>()) {}
EngineWrapper::~EngineWrapper() = default;
void EngineWrapper::initialize(const std::string& t3, const std::string& s3, int gpu, int threads, int context, int limit) {
    if (!std::filesystem::is_regular_file(t3) || !std::filesystem::is_regular_file(s3)) throw std::runtime_error("model file missing");
    impl_->t3 = t3;
    impl_->s3 = s3;
    impl_->gpu = gpu;
    impl_->threads = threads;
    impl_->context = context;
    impl_->limit = limit;
}
std::string EngineWrapper::create_session(const VoiceConfig& config) {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if (impl_->sessions.size() >= static_cast<std::size_t>(impl_->limit)) throw std::runtime_error("session limit reached");
    static std::mt19937 random(std::random_device{}());
    static std::uniform_int_distribution<> pick(0, 35);
    const char* chars = "0123456789abcdefghijklmnopqrstuvwxyz";
    std::string id;
    do {
        id.clear();
        for (int i = 0; i < 16; ++i) id += chars[pick(random)];
    } while (impl_->sessions.count(id));
    tts_cpp::chatterbox::EngineOptions options;
    options.t3_gguf_path = impl_->t3;
    options.s3gen_gguf_path = impl_->s3;
    options.n_gpu_layers = impl_->gpu;
    options.n_threads = impl_->threads;
    options.n_ctx = impl_->context;
    options.reference_audio = config.reference;
    options.language = config.language;
    options.seed = config.seed;
    options.n_predict = config.max_tokens;
    options.top_k = config.top_k;
    options.top_p = config.top_p;
    options.min_p = config.min_p;
    options.temperature = config.temperature;
    options.repeat_penalty = config.repeat;
    options.cfg_weight = config.cfg;
    options.exaggeration = config.exaggeration;
    options.cfm_steps = config.cfm_steps;
    options.stream_cfm_steps = config.cfm_steps;
    options.stream_first_chunk_tokens = config.first_chunk;
    options.stream_chunk_tokens = config.chunk;
    options.max_sentence_chars = config.max_sentence_chars;
    auto session = std::make_shared<Impl::Session>();
    session->engine = std::make_unique<tts_cpp::chatterbox::Engine>(options);
    impl_->sessions.emplace(id, std::move(session));
    return id;
}
void EngineWrapper::destroy_session(const std::string& id) {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    impl_->sessions.erase(id);
}
bool EngineWrapper::cancel(const std::string& id) {
    std::shared_ptr<Impl::Session> session;
    {
        std::lock_guard<std::mutex> lock(impl_->mutex);
        auto found = impl_->sessions.find(id);
        if (found == impl_->sessions.end()) return false;
        session = found->second;
    }
    session->engine->cancel();
    return true;
}
std::size_t EngineWrapper::session_count() const {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    return impl_->sessions.size();
}
void EngineWrapper::synthesize(const std::string& id, const std::string& text, std::function<void(const float*, std::size_t, int, bool)> callback) {
    std::shared_ptr<Impl::Session> session;
    {
        std::lock_guard<std::mutex> lock(impl_->mutex);
        session = impl_->sessions.at(id);
    }
    std::lock_guard<std::mutex> lock(session->synthesis);
    session->engine->synthesize(text, std::move(callback));
}
}
