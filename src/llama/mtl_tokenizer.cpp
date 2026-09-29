#include "mtl_tokenizer.h"
#include "common/gguf_file.h"
#include <algorithm>
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
bool lang_span_at(const std::string& text, size_t at, size_t& len, std::string& tag) {
    if (at >= text.size() || text[at] != '[') return false;
    size_t i = at + 1;
    size_t n = 0;
    while (i < text.size() && n < 3 && text[i] >= 'a' && text[i] <= 'z') {
        ++i;
        ++n;
    }
    if (n < 2 || i >= text.size() || text[i] != ']') return false;
    tag = text.substr(at + 1, n);
    len = n + 2;
    return true;
}
bool allowed_tag(const std::string& allowed, const std::string& tag) {
    return ("," + allowed + ",").find(",[" + tag + "],") != std::string::npos;
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
std::string MtlTokenizer::prepare(const std::string& text, const std::string& language, const std::string& allowed) {
    if (!allowed_tag(allowed, language))
        throw std::runtime_error("Unsupported language: " + language + "; GGUF offers " + allowed);
    size_t lead = 0;
    std::string lead_tag;
    std::string body = text;
    if (!lang_span_at(text, 0, lead, lead_tag)) body = "[" + language + "]" + text;
    for (size_t i = 0; i < body.size();) {
        size_t len = 0;
        std::string tag;
        if (!lang_span_at(body, i, len, tag)) {
            ++i;
            continue;
        }
        if (!allowed_tag(allowed, tag))
            throw std::runtime_error("Unsupported language: " + tag + "; GGUF offers " + allowed);
        i += len;
    }
    std::string spaced;
    for (char c : body) {
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
std::vector<int32_t> MtlTokenizer::tokenize(const std::string& text, const std::string& language, const std::string& allowed) const {
    const std::string prepared = prepare(text, language, allowed);
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
}
