#include "bpe.h"
#include <algorithm>
#include <limits>
#include <stdexcept>

namespace trident::gpt2 {
namespace {
bool letter(unsigned char c) { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c >= 0x80; }
bool digit(unsigned char c) { return c >= '0' && c <= '9'; }
bool gap(unsigned char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; }
}
Gpt2Bpe::Gpt2Bpe(const std::vector<std::string>& tokens, const std::vector<int32_t>& types, const std::vector<std::string>& merges) {
    if (tokens.empty()) throw std::runtime_error("Empty GPT-2 vocabulary");
    for (size_t i = 0; i < tokens.size(); ++i) {
        vocabulary_[tokens[i]] = int32_t(i);
        if (types[i] == 4) added_[tokens[i]] = int32_t(i);
    }
    for (size_t i = 0; i < merges.size(); ++i) ranks_[merges[i]] = int(i);
    int extra = 0;
    for (int byte = 0; byte < 256; ++byte) {
        int code = (byte >= 0x21 && byte <= 0x7e) || (byte >= 0xa1 && byte <= 0xac) || byte >= 0xae
            ? byte : 256 + extra++;
        if (code < 128) bytes_[byte] = char(code);
        else { bytes_[byte] += char(0xc0 | (code >> 6)); bytes_[byte] += char(0x80 | (code & 0x3f)); }
    }
}
void Gpt2Bpe::fragment(const std::string& text, std::vector<int32_t>& ids) const {
    auto emit = [&](size_t begin, size_t end) {
        std::vector<std::string> pieces;
        for (size_t i = begin; i < end; ++i) pieces.push_back(bytes_[(unsigned char)text[i]]);
        while (pieces.size() >= 2) {
            int best = std::numeric_limits<int>::max();
            size_t position = 0;
            for (size_t i = 0; i + 1 < pieces.size(); ++i) {
                auto rank = ranks_.find(pieces[i] + " " + pieces[i + 1]);
                if (rank != ranks_.end() && rank->second < best) { best = rank->second; position = i; }
            }
            if (best == std::numeric_limits<int>::max()) break;
            pieces[position] += pieces[position + 1];
            pieces.erase(pieces.begin() + position + 1);
        }
        for (const auto& piece : pieces) ids.push_back(vocabulary_.at(piece));
    };
    const char* contractions[] = {"'re", "'ve", "'ll", "'s", "'t", "'m", "'d"};
    for (size_t i = 0; i < text.size();) {
        bool contraction = false;
        for (const char* item : contractions) {
            const size_t n = std::char_traits<char>::length(item);
            if (text.compare(i, n, item) != 0) continue;
            emit(i, i + n);
            i += n;
            contraction = true;
            break;
        }
        if (contraction) continue;
        size_t start = i;
        if (text[i] == ' ' && i + 1 < text.size() && !gap((unsigned char)text[i + 1])) ++i;
        const unsigned char kind = (unsigned char)text[i];
        if (letter(kind)) while (i < text.size() && letter((unsigned char)text[i])) ++i;
        else if (digit(kind)) while (i < text.size() && digit((unsigned char)text[i])) ++i;
        else if (!gap(kind)) while (i < text.size() && !gap((unsigned char)text[i]) && !letter((unsigned char)text[i]) && !digit((unsigned char)text[i])) ++i;
        else while (i < text.size() && gap((unsigned char)text[i])) ++i;
        emit(start, i);
    }
}
std::vector<int32_t> Gpt2Bpe::tokenize(const std::string& text) const {
    struct Span { size_t start, length; int32_t id; };
    std::vector<Span> spans;
    for (const auto& entry : added_) {
        if (entry.first.empty()) continue;
        size_t position = 0;
        while ((position = text.find(entry.first, position)) != std::string::npos) {
            spans.push_back({position, entry.first.size(), entry.second}); position += entry.first.size();
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
        fragment(text.substr(cursor, span.start - cursor), ids);
        ids.push_back(span.id); cursor = span.start + span.length;
    }
    fragment(text.substr(cursor), ids);
    return ids;
}
}
