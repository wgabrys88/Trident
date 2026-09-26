#pragma once
#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

namespace trident::llama {
class MtlTokenizer {
    std::unordered_map<std::string, int32_t> vocabulary_;
    std::unordered_map<std::string, int32_t> added_;
    std::unordered_map<std::string, int> ranks_;
    int32_t unk_ = 1;
    static std::string prepare(const std::string& text, const std::string& language);
    void piece(const std::string& text, std::vector<int32_t>& ids) const;
public:
    explicit MtlTokenizer(const std::string& t3_path);
    std::vector<int32_t> tokenize(const std::string& text, const std::string& language) const;
};
}
