#pragma once
#include <regex>
#include <string>

namespace trident::gpt2 {
class EnglishText {
    std::regex date_, currency_, clock_, phone_, ordinal_, decimal_, integer_, seat_, phone_context_;
    static std::string cardinal(unsigned);
    static std::string ordinal(unsigned);
    static std::string digits(const std::string&);
    static std::string lower(std::string);
    static bool space(char);
    static bool word(unsigned char);
public:
    EnglishText();
    std::string prepare(const std::string&) const;
};
}
