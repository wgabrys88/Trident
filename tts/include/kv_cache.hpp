#pragma once

#include <cstddef>
#include <cstdint>
#include <list>
#include <string>
#include <unordered_map>
#include <vector>

namespace tts {

struct PrefixKV {
    std::string key;
    std::vector<int32_t> tokens;
    int n_past = 0;
    int prompt_len = 0;
    // Opaque LLaMA K/V blob. Chatterbox T3 (MTL Llama-520M or GPT-2 turbo)
    // writes its prefix state here so a repeated prompt skips eval_prompt*.
    std::vector<uint8_t> blob;
    double saved_ms = 0;
};

class PrefixKVCache {
public:
    explicit PrefixKVCache(size_t capacity = 16) : capacity_(capacity ? capacity : 16) {}

    static std::string make_key(const std::string& family, const std::string& language,
                                const std::vector<int32_t>& tokens, int prefix_n);

    bool get(const std::string& key, PrefixKV& out);
    void put(PrefixKV entry);
    void clear();

    size_t size() const { return map_.size(); }
    size_t capacity() const { return capacity_; }
    uint64_t hits() const { return hits_; }
    uint64_t misses() const { return misses_; }
    uint64_t evictions() const { return evictions_; }

private:
    size_t capacity_;
    std::list<std::string> order_;
    std::unordered_map<std::string, std::pair<PrefixKV, std::list<std::string>::iterator>> map_;
    uint64_t hits_ = 0, misses_ = 0, evictions_ = 0;
};

} // namespace tts
