#include "bpe.h"
#include <algorithm>
#include <limits>
#include <stdexcept>
#include <tuple>

namespace trident::gpt2 {
Gpt2Bpe::Gpt2Bpe(const std::vector<std::string>& tokens, const std::vector<int32_t>& types, const std::vector<std::string>& merges)
    : words_(R"('s|'t|'re|'ve|'m|'ll|'d| ?[[:alpha:]]+| ?[[:digit:]]+| ?[^\s[:alpha:][:digit:]]+|\s+(?!\S)|\s+)", std::regex::optimize) {
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
    for (auto word = std::sregex_iterator(text.begin(), text.end(), words_); word != std::sregex_iterator(); ++word) {
        std::vector<std::string> pieces;
        for (unsigned char byte : word->str()) pieces.push_back(bytes_[byte]);
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
std::string Gpt2Bpe::punc_norm(std::string text) const {
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
            text.replace(position, replacement.first.size(), replacement.second); position += replacement.second.size();
        }
    }
    text.erase(text.find_last_not_of(" \t\n\r") + 1);
    if (text.empty()) throw std::runtime_error("Empty normalized punctuation input");
    if (std::string(".!?-,").find(text.back()) == std::string::npos) text += '.';
    return text;
}
}
