#pragma once

#include "audio.hpp"
#include "family.hpp"
#include "kv_cache.hpp"
#include "ring_buffer.hpp"
#include "speaker_cache.hpp"

#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace tts {

struct Voice {
    std::string reference, language;
    double reference_mtime = 0;
    int seed = 42, max_tokens = 768, top_k = 0, cfm_steps = 7, chunk_chars = 180;
    float exaggeration = 0.5f, cfg = 0.5f, temperature = 0.8f, repeat = 1.2f, min_p = 0.05f, top_p = 1.0f;
};

struct CacheStats {
    uint64_t speaker_hits = 0, speaker_misses = 0, speaker_evictions = 0;
    uint64_t kv_hits = 0, kv_misses = 0, kv_evictions = 0;
    double speaker_saved_ms = 0, kv_saved_ms = 0;
};

class EngineWrapper {
public:
    EngineWrapper();
    ~EngineWrapper();

    void initialize(const std::string& t3, const std::string& s3, int gpu, int threads, int context);
    void LoadChatterboxModel(const std::string& name, const std::string& t3_path, const std::string& s3_path);
    void set_family(Family family);
    void set_params(const EngineKnobs& knobs);

    void prepare(const Voice& voice, const std::string& text);
    bool step(const std::function<void(int, int, const std::vector<float>&, const std::vector<float>&)>& on_pack);
    bool busy() const;
    Speech finish();
    void cancel();

    RingBuffer& ring() { return ring_; }
    const RingBuffer& ring() const { return ring_; }
    SpeakerCache& speakers() { return speakers_; }
    PrefixKVCache& prefixes() { return prefixes_; }
    CacheStats stats() const;
    Family family() const { return family_; }
    const FamilyPolicy& policy() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
    Family family_ = Family::Turbo;
    SpeakerCache speakers_;
    PrefixKVCache prefixes_;
    RingBuffer ring_{1 << 16};
    mutable CacheStats last_stats_{};
};

Voice knobs_to_voice(const EngineKnobs& knobs, int chunk_chars);
EngineKnobs voice_to_knobs(const Voice& voice);

} // namespace tts
