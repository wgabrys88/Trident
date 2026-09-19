#pragma once
#include <icu.h>
#include <memory>
#include <string>
#include <vector>

namespace trident::llama {
class MtlNumbers {
    using Chars = std::vector<UChar>;
    std::unique_ptr<UNumberFormat, decltype(&unum_close)> format_;
    static Chars decode(const std::string&);
    static std::string encode(const Chars&);
    static bool word(UChar32);
    static bool edge(const Chars&, bool first);
    static bool has_digit(const Chars&);
    Chars decimal(const std::string&) const;
    Chars digits(const std::string&) const;
public:
    explicit MtlNumbers(const std::string& language);
    std::string verbalize(const std::string&) const;
};
}
