#include "kv_cache.hpp"

#include <sstream>

namespace tts {

std::string PrefixKVCache::make_key(const std::string& family, const std::string& language,
                                    const std::vector<int32_t>& tokens, int prefix_n) {
    std::ostringstream oss;
    oss << family << '\n' << language << '\n';
    const int n = prefix_n < 0 ? 0 : prefix_n;
    const int take = n < static_cast<int>(tokens.size()) ? n : static_cast<int>(tokens.size());
    oss << take << '\n';
    for (int i = 0; i < take; ++i) oss << tokens[static_cast<size_t>(i)] << ' ';
    return oss.str();
}

bool PrefixKVCache::get(const std::string& key, PrefixKV& out) {
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

void PrefixKVCache::put(PrefixKV entry) {
    const std::string key = entry.key;
    auto it = map_.find(key);
    if (it != map_.end()) {
        it->second.first = std::move(entry);
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
    map_.emplace(key, std::make_pair(std::move(entry), order_.begin()));
}

void PrefixKVCache::clear() {
    map_.clear();
    order_.clear();
}

} // namespace tts
