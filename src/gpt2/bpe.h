#pragma once
#include <array>
#include <cstdint>
#include <regex>
#include <string>
#include <unordered_map>
#include <vector>

namespace trident::gpt2 {
class Gpt2Bpe {
    std::unordered_map<std::string, int32_t> vocabulary_;
    std::unordered_map<std::string, int> ranks_;
    std::array<std::string, 256> bytes_;
    std::regex words_;
    void fragment(const std::string&, std::vector<int32_t>&) const;
public:
    Gpt2Bpe(const std::vector<std::string>& tokens, const std::vector<std::string>& merges);
    std::vector<int32_t> tokenize(const std::string&) const;
    std::string punc_norm(std::string text) const;
};
}
