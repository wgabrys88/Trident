#include "mtl_numbers.h"
#include <stdexcept>

namespace trident::llama {
MtlNumbers::MtlNumbers(const std::string& language) : format_(nullptr, unum_close) {
    UErrorCode status = U_ZERO_ERROR;
    format_.reset(unum_open(UNUM_SPELLOUT, nullptr, 0, language.c_str(), nullptr, &status));
    if (U_FAILURE(status)) throw std::runtime_error("ICU number formatter creation failed");
}
MtlNumbers::Chars MtlNumbers::decode(const std::string& text) {
    UErrorCode status = U_ZERO_ERROR;
    int32_t count;
    u_strFromUTF8(nullptr, 0, &count, text.data(), int32_t(text.size()), &status);
    status = U_ZERO_ERROR;
    Chars result(count);
    u_strFromUTF8(result.data(), count, nullptr, text.data(), int32_t(text.size()), &status);
    if (U_FAILURE(status)) throw std::runtime_error("ICU UTF-8 decoding failed");
    return result;
}
std::string MtlNumbers::encode(const Chars& text) {
    UErrorCode status = U_ZERO_ERROR;
    int32_t count;
    u_strToUTF8(nullptr, 0, &count, text.data(), int32_t(text.size()), &status);
    status = U_ZERO_ERROR;
    std::string result(count, '\0');
    u_strToUTF8(result.data(), count, nullptr, text.data(), int32_t(text.size()), &status);
    if (U_FAILURE(status)) throw std::runtime_error("ICU UTF-8 encoding failed");
    return result;
}
bool MtlNumbers::word(UChar32 c) {
    int8_t type = u_charType(c);
    return u_hasBinaryProperty(c, UCHAR_ALPHABETIC) || type == U_NON_SPACING_MARK || type == U_COMBINING_SPACING_MARK
        || type == U_ENCLOSING_MARK || type == U_DECIMAL_DIGIT_NUMBER || type == U_CONNECTOR_PUNCTUATION || c == 0x200c || c == 0x200d;
}
bool MtlNumbers::edge(const Chars& text, bool first) {
    if (text.empty()) return false;
    int32_t offset = first ? 0 : int32_t(text.size());
    UChar32 c;
    if (first) U16_NEXT(text.data(), offset, int32_t(text.size()), c);
    else U16_PREV(text.data(), 0, offset, c);
    return word(c);
}
bool MtlNumbers::has_digit(const Chars& text) {
    for (int32_t i = 0; i < int32_t(text.size());) {
        UChar32 c; U16_NEXT(text.data(), i, int32_t(text.size()), c);
        if (u_charDigitValue(c) >= 0) return true;
    }
    return false;
}
MtlNumbers::Chars MtlNumbers::decimal(const std::string& value) const {
    UErrorCode status = U_ZERO_ERROR;
    int32_t count = unum_formatDecimal(format_.get(), value.data(), int32_t(value.size()), nullptr, 0, nullptr, &status);
    status = U_ZERO_ERROR;
    Chars formatted(count), result;
    unum_formatDecimal(format_.get(), value.data(), int32_t(value.size()), formatted.data(), count, nullptr, &status);
    if (U_FAILURE(status)) throw std::runtime_error("ICU number formatting failed");
    for (int32_t i = 0; i < count;) {
        int32_t start = i;
        UChar32 c; U16_NEXT(formatted.data(), i, count, c);
        if (u_charType(c) != U_FORMAT_CHAR) result.insert(result.end(), formatted.begin() + start, formatted.begin() + i);
    }
    return result;
}
MtlNumbers::Chars MtlNumbers::digits(const std::string& value) const {
    Chars result;
    for (char digit : value) {
        auto spoken = decimal(std::string(1, digit));
        if (!result.empty()) result.push_back(UChar(' '));
        result.insert(result.end(), spoken.begin(), spoken.end());
    }
    return result;
}
std::string MtlNumbers::verbalize(const std::string& text) const {
    auto input = decode(text);
    if (!has_digit(input)) return text;
    Chars output;
    int32_t cursor = 0, offset = 0;
    while (offset < int32_t(input.size())) {
        int32_t next = offset;
        UChar32 c; U16_NEXT(input.data(), next, int32_t(input.size()), c);
        if (u_charDigitValue(c) < 0) { offset = next; continue; }
        int32_t begin = offset;
        std::string number;
        while (offset < int32_t(input.size())) {
            next = offset; U16_NEXT(input.data(), next, int32_t(input.size()), c);
            int digit = u_charDigitValue(c);
            if (digit < 0) break;
            number += char('0' + digit); offset = next;
        }
        auto spoken = number.size() > 1 && number.front() == '0' ? digits(number) : decimal(number);
        if (has_digit(spoken)) spoken = digits(number);
        output.insert(output.end(), input.begin() + cursor, input.begin() + begin);
        if (edge(output, false) && edge(spoken, true)) output.push_back(UChar(' '));
        output.insert(output.end(), spoken.begin(), spoken.end());
        if (offset < int32_t(input.size())) {
            next = offset; U16_NEXT(input.data(), next, int32_t(input.size()), c);
            if (word(c) && edge(spoken, false)) output.push_back(UChar(' '));
        }
        cursor = offset;
    }
    output.insert(output.end(), input.begin() + cursor, input.end());
    return encode(output);
}
}
