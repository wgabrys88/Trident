#pragma once
#include <memory>
#include <string>
#include <vector>

namespace tts {

struct Voice {
    std::string reference, language;
    double reference_mtime = 0;
    int seed = 42, max_tokens = 1000, top_k = 1000, cfm_steps = 5, chunk_chars = 300;
    float exaggeration = 0.5f, cfg = 0.5f, temperature = 0.8f, repeat = 1.2f, min_p = 0.05f, top_p = 0.95f;
};

struct Speech {
    std::vector<float> pcm;
    double t3_ms = 0, s3gen_ms = 0;
    int chunks = 1;
};

class EngineWrapper {
public:
    EngineWrapper();
    ~EngineWrapper();
    void initialize(const std::string& t3, const std::string& s3, int gpu, int threads, int context);
    Speech speak(const Voice&, const std::string& text);
    void cancel();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}
