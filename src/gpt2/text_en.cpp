#include "text_en.h"
#include <algorithm>
#include <cctype>
#include <iterator>

namespace trident::gpt2 {
EnglishText::EnglishText()
    : date_(R"((January|February|March|April|May|June|July|August|September|October|November|December) ([0-9]{1,2}), ([0-9]{4}))", std::regex::icase),
      currency_(R"(\$([0-9]{1,6})(\.([0-9]{2}))?)"),
      clock_(R"(([0-9]{1,2}):([0-9]{2}) ([ap])\.m\.)", std::regex::icase),
      phone_(R"(([0-9]{3})-([0-9]{4}))"), ordinal_(R"(([0-9]{1,2})(st|nd|rd|th))", std::regex::icase),
      decimal_(R"(([0-9]{0,6})\.([0-9]+))"), integer_(R"([0-9]{1,6})"), seat_(R"(([0-9]{1,6})([A-Za-z]))"),
      phone_context_(R"((call|phone|telephone)\b[^.!?\n]*$)", std::regex::icase) {}
std::string EnglishText::cardinal(unsigned number) {
    const char* small[] = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"};
    const char* tens[] = {"", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"};
    if (number < 20) return small[number];
    if (number < 100) return std::string(tens[number / 10]) + (number % 10 ? "-" + cardinal(number % 10) : "");
    if (number < 1000) return cardinal(number / 100) + " hundred" + (number % 100 ? " " + cardinal(number % 100) : "");
    return cardinal(number / 1000) + " thousand" + (number % 1000 ? " " + cardinal(number % 1000) : "");
}
std::string EnglishText::ordinal(unsigned number) {
    const char* small[] = {"", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
        "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth", "twentieth"};
    if (number <= 20) return small[number];
    if (number == 30) return "thirtieth";
    return std::string(number < 30 ? "twenty-" : "thirty-") + small[number % 10];
}
std::string EnglishText::digits(const std::string& value) {
    std::string result;
    for (char c : value) { if (!result.empty()) result += ' '; result += cardinal(c - '0'); }
    return result;
}
std::string EnglishText::lower(std::string value) {
    for (char& c : value) if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
    return value;
}
bool EnglishText::space(char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; }
bool EnglishText::word(unsigned char c) { return c >= 128 || std::isalnum(c) || c == '_'; }
std::string EnglishText::prepare(const std::string& text) const {
    std::string result;
    for (size_t offset = 0; offset < text.size();) {
        if (space(text[offset])) {
            do { ++offset; } while (offset < text.size() && space(text[offset]));
            result += ' '; continue;
        }
        std::smatch match;
        size_t consumed = 0;
        std::string replacement;
        auto at = [&](const std::regex& expression) {
            return std::regex_search(text.begin() + std::ptrdiff_t(offset), text.end(), match, expression,
                                     std::regex_constants::match_continuous);
        };
        auto boundary = [&](size_t count) {
            size_t end = offset + count;
            return end == text.size() || (!word(static_cast<unsigned char>(text[end])) && text[end] != '.' && text[end] != '-' && text[end] != ':')
                || (text[end] == '.' && (end + 1 == text.size() || space(text[end + 1])));
        };
        if (offset == 0 || !word(static_cast<unsigned char>(text[offset - 1]))) {
            size_t end = offset;
            while (end < text.size() && !space(text[end])) ++end;
            std::string atom = text.substr(offset, end - offset);
            bool letters = false, numbers = false;
            for (unsigned char c : atom) { letters |= std::isalpha(c) != 0; numbers |= std::isdigit(c) != 0; }
            bool seat_context = offset >= 5 && lower(text.substr(offset - 5, 5)) == "seat ";
            bool ordinal_suffix = at(ordinal_) && boundary(match.length());
            if (atom.find("://") != std::string::npos || atom.find('@') != std::string::npos || atom.find('_') != std::string::npos
                || (letters && numbers && !seat_context && !ordinal_suffix && atom.find(':') == std::string::npos)) {
                result += atom; offset = end; continue;
            }
            if (at(date_) && boundary(match.length())) {
                unsigned day = std::stoul(match[2]), year = std::stoul(match[3]);
                const std::string months[] = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"};
                const int days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
                auto month = std::find(std::begin(months), std::end(months), lower(match[1])) - std::begin(months);
                int limit = days[month] + (month == 1 && (year % 400 == 0 || (year % 4 == 0 && year % 100 != 0)));
                if (day >= 1 && day <= unsigned(limit)) { consumed = match.length(); replacement = match[1].str() + " " + ordinal(day) + ", " + cardinal(year); }
            }
            if (!consumed && at(currency_) && boundary(match.length())) {
                unsigned dollars = std::stoul(match[1]), cents = match[3].matched ? std::stoul(match[3]) : 0;
                consumed = match.length(); replacement = cardinal(dollars) + (dollars == 1 ? " dollar" : " dollars");
                if (cents) replacement += " and " + cardinal(cents) + (cents == 1 ? " cent" : " cents");
            }
            if (!consumed && at(clock_) && boundary(match.length())) {
                unsigned hour = std::stoul(match[1]), minute = std::stoul(match[2]);
                if (hour >= 1 && hour <= 12 && minute < 60) {
                    consumed = match.length(); replacement = cardinal(hour) + (minute == 0 ? " o clock" : minute < 10 ? " oh " + cardinal(minute) : " " + cardinal(minute)) + " " + lower(match[3]) + " m";
                }
            }
            if (!consumed && at(phone_) && boundary(match.length()) && std::regex_search(text.substr(0, offset), phone_context_)) {
                consumed = match.length(); replacement = digits(match[1]) + ", " + digits(match[2]);
            }
            if (!consumed && at(ordinal_) && boundary(match.length())) {
                unsigned number = std::stoul(match[1]);
                std::string suffix = (number % 100 >= 11 && number % 100 <= 13) ? "th" : number % 10 == 1 ? "st" : number % 10 == 2 ? "nd" : number % 10 == 3 ? "rd" : "th";
                if (number >= 1 && number <= 31 && lower(match[2]) == suffix) { consumed = match.length(); replacement = ordinal(number); }
            }
            if (!consumed && at(seat_) && boundary(match.length()) && seat_context) {
                consumed = match.length(); replacement = cardinal(std::stoul(match[1])) + " " + match[2].str();
            }
            if (!consumed && at(decimal_) && boundary(match.length())) {
                consumed = match.length(); replacement = cardinal(match[1].length() ? std::stoul(match[1]) : 0) + " point " + digits(match[2]);
            }
            if (!consumed && at(integer_) && boundary(match.length())
                && (offset == 0 || (text[offset - 1] != '-' && text[offset - 1] != '.' && text[offset - 1] != '$' && text[offset - 1] != ':'))) {
                consumed = match.length(); replacement = cardinal(std::stoul(match[0]));
            }
        }
        if (consumed) { result += replacement; offset += consumed; }
        else result += text[offset++];
    }
    return result;
}
}
