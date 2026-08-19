#pragma once
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace tts {

constexpr int kGlue = 2880;

struct Voice {
    std::string reference, language;
    double reference_mtime = 0;
    int seed = 0, max_tokens = 0, top_k = 0, cfm_steps = 0, chunk_chars = 0;
    float exaggeration = 0, cfg_weight = 0, temperature = 0, repeat_penalty = 0, min_p = 0, top_p = 0;
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
    void prepare(const Voice&, const std::string& text);
    bool step(const std::function<void(int, int, const std::vector<float>&, const std::vector<float>&)>& on_pack);
    bool busy() const;
    Speech finish();
    void cancel();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}
