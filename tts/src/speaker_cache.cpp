#include "speaker_cache.hpp"

#include <sstream>

namespace tts {

std::string SpeakerCache::make_key(const std::string& path, double mtime, const std::string& language) {
    std::ostringstream oss;
    oss << path << '\n' << mtime << '\n' << language;
    return oss.str();
}

bool SpeakerCache::get(const std::string& key, SpeakerEmbedding& out) {
    auto it = map_.find(key);
    if (it == map_.end()) {
        ++misses_;
        return false;
    }
    order_.splice(order_.begin(), order_, it->second.second);
    it->second.second = order_.begin();
    out = it->second.first;
    ++hits_;
    return true;
}

void SpeakerCache::put(SpeakerEmbedding emb) {
    const std::string key = emb.key;
    auto it = map_.find(key);
    if (it != map_.end()) {
        it->second.first = std::move(emb);
        order_.splice(order_.begin(), order_, it->second.second);
        it->second.second = order_.begin();
        return;
    }
    while (map_.size() >= capacity_) {
        const std::string& old = order_.back();
        map_.erase(old);
        order_.pop_back();
        ++evictions_;
    }
    order_.push_front(key);
    map_.emplace(key, std::make_pair(std::move(emb), order_.begin()));
}

void SpeakerCache::clear() {
    map_.clear();
    order_.clear();
}

} // namespace tts
