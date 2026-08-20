#pragma once

#include <cstddef>
#include <cstdint>
#include <list>
#include <string>
#include <unordered_map>
#include <vector>

namespace tts {

struct SpeakerEmbedding {
    std::string key;
    std::vector<float> se;          // Resemble voice encoder, 256-d
    std::vector<int32_t> cond_tok;  // S3TokenizerV2 cond tokens
    std::vector<float> prompt_feat; // S3Gen prompt feat
    std::vector<float> campplus;    // CAMPPlus speaker embedding
    int prompt_feat_rows = 0;
    double encode_ms = 0;
};

// LRU of speaker embeddings. Same reference (path + mtime) reuses the
// VoiceEncoder / S3Tokenizer / CAMPPlus work instead of re-running it.
class SpeakerCache {
public:
    explicit SpeakerCache(size_t capacity = 8) : capacity_(capacity ? capacity : 8) {}

    static std::string make_key(const std::string& path, double mtime, const std::string& language);

    bool get(const std::string& key, SpeakerEmbedding& out);
    void put(SpeakerEmbedding emb);
    void clear();

    size_t size() const { return map_.size(); }
    size_t capacity() const { return capacity_; }
    uint64_t hits() const { return hits_; }
    uint64_t misses() const { return misses_; }
    uint64_t evictions() const { return evictions_; }

private:
    size_t capacity_;
    std::list<std::string> order_;
    std::unordered_map<std::string, std::pair<SpeakerEmbedding, std::list<std::string>::iterator>> map_;
    uint64_t hits_ = 0, misses_ = 0, evictions_ = 0;
};

} // namespace tts
