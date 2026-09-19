#pragma once
#include "trident/llama_engine.h"
#include "common/pipe.h"
#include <cstdint>

namespace trident::llama {
class MtlTokenizer {
    Handle input_, output_, process_;
    static std::wstring wide(const std::string&);
    static std::wstring quote(const std::wstring&);
    std::vector<uint8_t> request(char mode, const std::string&);
public:
    explicit MtlTokenizer(const TokenizerPaths&);
    ~MtlTokenizer();
    std::string punctuation(const std::string&);
    std::vector<int32_t> tokenize(const std::string&);
};
}
