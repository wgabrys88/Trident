#include "engine_wrapper.hpp"
#include <tts-cpp/chatterbox/engine.h>
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

Speech EngineWrapper::speak(const Voice& voice, const std::string& text) {
    std::lock_guard<std::mutex> synth(impl_->synth);
    std::shared_ptr<tts_cpp::chatterbox::Engine> engine;
    {
        std::lock_guard<std::mutex> lock(impl_->state);
        const bool same = impl_->engine
            && impl_->voice.reference == voice.reference
            && impl_->voice.language == voice.language
            && impl_->voice.reference_mtime == voice.reference_mtime;
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
        engine = impl_->engine;
    }
    auto result = engine->synthesize(text);
    return {std::move(result.pcm), result.t3_ms, result.s3gen_ms};
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
