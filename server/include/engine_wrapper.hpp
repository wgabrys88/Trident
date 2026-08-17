#pragma once
#include <cstddef>
#include <functional>
#include <memory>
#include <string>

namespace tts {
struct VoiceConfig {
    std::string reference, language;
    int seed, max_tokens, top_k, cfm_steps, first_chunk, chunk, max_sentence_chars;
    float exaggeration, cfg, temperature, repeat, min_p, top_p;
};

class EngineWrapper {
public:
    EngineWrapper();
    ~EngineWrapper();
    void initialize(const std::string&, const std::string&, int, int, int, int);
    std::string create_session(const VoiceConfig&);
    void destroy_session(const std::string&);
    bool cancel(const std::string&);
    std::size_t session_count() const;
    void synthesize(const std::string&, const std::string&, std::function<void(const float*, std::size_t, int, bool)>);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
