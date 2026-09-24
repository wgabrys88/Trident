#include "mtl_tokenizer.h"
#include "common/gguf_file.h"
#include <algorithm>
#include <icu.h>
#include <limits>
#include <stdexcept>

namespace trident::llama {
namespace {
std::vector<std::string> codepoints(const std::string& text) {
    std::vector<std::string> out;
    for (size_t i = 0; i < text.size();) {
        unsigned char lead = text[i];
        size_t n = lead < 0x80 ? 1 : lead < 0xE0 ? 2 : lead < 0xF0 ? 3 : 4;
        if (i + n > text.size()) n = 1;
        out.push_back(text.substr(i, n));
        i += n;
    }
    return out;
}
std::string utf8_from(const std::vector<UChar>& text) {
    UErrorCode status = U_ZERO_ERROR;
    int32_t count;
    u_strToUTF8(nullptr, 0, &count, text.data(), int32_t(text.size()), &status);
    status = U_ZERO_ERROR;
    std::string result(count, '\0');
    u_strToUTF8(result.data(), count, nullptr, text.data(), int32_t(text.size()), &status);
    if (U_FAILURE(status)) throw std::runtime_error("ICU UTF-8 encoding failed");
    return result;
}
std::vector<UChar> utf8_to(const std::string& text) {
    UErrorCode status = U_ZERO_ERROR;
    int32_t count;
    u_strFromUTF8(nullptr, 0, &count, text.data(), int32_t(text.size()), &status);
    status = U_ZERO_ERROR;
    std::vector<UChar> result(count);
    u_strFromUTF8(result.data(), count, nullptr, text.data(), int32_t(text.size()), &status);
    if (U_FAILURE(status)) throw std::runtime_error("ICU UTF-8 decoding failed");
    return result;
}
}
MtlTokenizer::MtlTokenizer(const std::string& t3_path) {
    GgufFile file(t3_path);
    auto tokens = file.strings("tokenizer.ggml.tokens");
    auto types = file.ints("tokenizer.ggml.token_type");
    auto merges = file.strings("tokenizer.ggml.merges");
    for (size_t i = 0; i < tokens.size(); ++i) {
        vocabulary_[tokens[i]] = int32_t(i);
        if (i < types.size() && (types[i] == 3 || types[i] == 4)) added_[tokens[i]] = int32_t(i);
    }
    auto unk = vocabulary_.find("[UNK]");
    if (unk != vocabulary_.end()) unk_ = unk->second;
    for (size_t i = 0; i < merges.size(); ++i) ranks_[merges[i]] = int(i);
}
std::string MtlTokenizer::prepare(const std::string& text, const std::string& language) {
    auto chars = utf8_to(text);
    UErrorCode status = U_ZERO_ERROR;
    const UNormalizer2* norm = unorm2_getNFKDInstance(&status);
    if (U_FAILURE(status)) throw std::runtime_error("ICU NFKD unavailable");
    status = U_ZERO_ERROR;
    int32_t need = unorm2_normalize(norm, chars.data(), int32_t(chars.size()), nullptr, 0, &status);
    status = U_ZERO_ERROR;
    std::vector<UChar> folded(need);
    unorm2_normalize(norm, chars.data(), int32_t(chars.size()), folded.data(), need, &status);
    if (U_FAILURE(status)) throw std::runtime_error("ICU NFKD failed");
    status = U_ZERO_ERROR;
    int32_t lower_need = u_strToLower(nullptr, 0, folded.data(), need, "", &status);
    status = U_ZERO_ERROR;
    std::vector<UChar> lower(lower_need);
    u_strToLower(lower.data(), lower_need, folded.data(), need, "", &status);
    if (U_FAILURE(status)) throw std::runtime_error("ICU lowercase failed");
    std::string out = "[" + language + "]" + utf8_from(lower);
    std::string spaced;
    for (char c : out) {
        if (c == ' ') spaced += "[SPACE]";
        else spaced += c;
    }
    return spaced;
}
void MtlTokenizer::piece(const std::string& text, std::vector<int32_t>& ids) const {
    auto parts = codepoints(text);
    while (parts.size() >= 2) {
        int best = std::numeric_limits<int>::max();
        size_t position = 0;
        for (size_t i = 0; i + 1 < parts.size(); ++i) {
            auto rank = ranks_.find(parts[i] + " " + parts[i + 1]);
            if (rank != ranks_.end() && rank->second < best) { best = rank->second; position = i; }
        }
        if (best == std::numeric_limits<int>::max()) break;
        parts[position] += parts[position + 1];
        parts.erase(parts.begin() + position + 1);
    }
    for (const auto& part : parts) {
        auto id = vocabulary_.find(part);
        ids.push_back(id == vocabulary_.end() ? unk_ : id->second);
    }
}
std::vector<int32_t> MtlTokenizer::tokenize(const std::string& text, const std::string& language) const {
    const std::string prepared = prepare(text, language);
    struct Span { size_t start, length; int32_t id; };
    std::vector<Span> spans;
    for (const auto& entry : added_) {
        if (entry.first.empty()) continue;
        size_t position = 0;
        while ((position = prepared.find(entry.first, position)) != std::string::npos) {
            spans.push_back({position, entry.first.size(), entry.second});
            position += entry.first.size();
        }
    }
    std::sort(spans.begin(), spans.end(), [](const Span& a, const Span& b) {
        if (a.start != b.start) return a.start < b.start;
        if (a.length != b.length) return a.length > b.length;
        return a.id < b.id;
    });
    std::vector<int32_t> ids;
    size_t cursor = 0;
    for (const auto& span : spans) {
        if (span.start < cursor) continue;
        piece(prepared.substr(cursor, span.start - cursor), ids);
        ids.push_back(span.id);
        cursor = span.start + span.length;
    }
    piece(prepared.substr(cursor), ids);
    return ids;
}
std::string MtlTokenizer::punctuation(std::string text) {
    if (text.empty()) throw std::runtime_error("Empty punctuation input");
    if (text[0] >= 'a' && text[0] <= 'z') text[0] += 'A' - 'a';
    std::string collapsed;
    bool space = false;
    for (char c : text) {
        if (c != ' ' || !space) collapsed += c;
        space = c == ' ';
    }
    text = std::move(collapsed);
    const std::pair<std::string, std::string> replacements[] = {
        {"\xe2\x80\xa6", ", "}, {":", ","}, {"\xe2\x80\x94", "-"}, {"\xe2\x80\x93", "-"},
        {" ,", ","}, {"\xe2\x80\x9c", "\""}, {"\xe2\x80\x9d", "\""}, {"\xe2\x80\x98", "'"}, {"\xe2\x80\x99", "'"},
    };
    for (const auto& replacement : replacements) {
        size_t position = 0;
        while ((position = text.find(replacement.first, position)) != std::string::npos) {
            text.replace(position, replacement.first.size(), replacement.second);
            position += replacement.second.size();
        }
    }
    text.erase(text.find_last_not_of(" \t\n\r") + 1);
    if (text.empty()) throw std::runtime_error("Empty normalized punctuation input");
    if (std::string(".!?-,").find(text.back()) == std::string::npos) text += '.';
    return text;
}
}
