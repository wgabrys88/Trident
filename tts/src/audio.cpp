#include "audio.hpp"
#include "cli.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <limits>
#include <stdexcept>

namespace tts {

std::vector<std::string> pack_text(const std::string& text, int limit) {
    if (limit < 40) limit = 40;
    if (text.empty()) return {text};
    auto is_ws = [](unsigned char c) { return std::isspace(c) != 0; };
    auto trim_right = [&](std::string& s) {
        while (!s.empty() && is_ws(static_cast<unsigned char>(s.back()))) s.pop_back();
    };
    auto glue_text = [&](std::string& dst, const std::string& src) {
        if (src.empty()) return;
        if (!dst.empty() && !is_ws(static_cast<unsigned char>(dst.back())) &&
            !is_ws(static_cast<unsigned char>(src.front())))
            dst += ' ';
        dst += src;
    };

    std::vector<std::string> sentences;
    std::string cur;
    size_t i = 0;
    while (i < text.size()) {
        cur += text[i];
        const char c = text[i];
        const bool at_end = i + 1 == text.size();
        const bool next_ws = !at_end && is_ws(static_cast<unsigned char>(text[i + 1]));
        if ((c == '.' || c == '?' || c == '!') && (at_end || next_ws)) {
            size_t j = i + 1;
            while (j < text.size() && is_ws(static_cast<unsigned char>(text[j]))) cur += text[j++];
            sentences.push_back(cur);
            cur.clear();
            i = j;
        } else {
            ++i;
        }
    }
    if (!cur.empty()) sentences.push_back(cur);

    std::vector<std::string> refined;
    for (auto& sentence : sentences) {
        if (utf8_chars(sentence) <= limit) {
            refined.push_back(std::move(sentence));
            continue;
        }
        std::string acc;
        size_t k = 0;
        while (k < sentence.size()) {
            acc += sentence[k];
            const char c = sentence[k];
            const bool next_ws = k + 1 < sentence.size() && is_ws(static_cast<unsigned char>(sentence[k + 1]));
            if ((c == ',' || c == ':' || c == ';') && next_ws && utf8_chars(acc) > limit / 2) {
                size_t j = k + 1;
                while (j < sentence.size() && is_ws(static_cast<unsigned char>(sentence[j]))) acc += sentence[j++];
                refined.push_back(acc);
                acc.clear();
                k = j;
                continue;
            }
            ++k;
        }
        if (!acc.empty()) refined.push_back(acc);
    }

    std::vector<std::string> packed;
    for (auto& sentence : refined) {
        trim_right(sentence);
        if (sentence.empty()) continue;
        if (packed.empty()) {
            packed.push_back(std::move(sentence));
            continue;
        }
        const int extra = (!is_ws(static_cast<unsigned char>(packed.back().back())) &&
                           !is_ws(static_cast<unsigned char>(sentence.front()))) ? 1 : 0;
        if (utf8_chars(packed.back()) + extra + utf8_chars(sentence) <= limit)
            glue_text(packed.back(), sentence);
        else
            packed.push_back(std::move(sentence));
    }
    if (packed.size() >= 2 && utf8_chars(packed.back()) * 2 < limit) {
        const int extra = (!is_ws(static_cast<unsigned char>(packed[packed.size() - 2].back())) &&
                           !is_ws(static_cast<unsigned char>(packed.back().front()))) ? 1 : 0;
        if (utf8_chars(packed[packed.size() - 2]) + extra + utf8_chars(packed.back()) <= limit) {
            glue_text(packed[packed.size() - 2], packed.back());
            packed.pop_back();
        }
    }
    return packed.empty() ? std::vector<std::string>{text} : packed;
}

std::vector<std::string> pack_text_staged(const std::string& text, int first_limit, int later_limit) {
    if (first_limit < 1 || later_limit < 1) throw std::invalid_argument("chunk limit must be positive");
    if (later_limit <= first_limit) return pack_text(text, later_limit);
    auto base = pack_text(text, first_limit);
    if (base.size() <= 1) return base;

    auto is_ws = [](unsigned char c) { return std::isspace(c) != 0; };
    auto glue_text = [&](std::string& dst, const std::string& src) {
        if (!dst.empty() && !src.empty() &&
            !is_ws(static_cast<unsigned char>(dst.back())) &&
            !is_ws(static_cast<unsigned char>(src.front())))
            dst += ' ';
        dst += src;
    };

    std::vector<std::string> out;
    out.reserve(base.size());
    out.push_back(std::move(base[0]));
    for (size_t i = 1; i < base.size(); ++i) {
        std::string piece = std::move(base[i]);
        if (out.size() == 1) {
            out.push_back(std::move(piece));
            continue;
        }
        const int extra = (!is_ws(static_cast<unsigned char>(out.back().back())) &&
                           !is_ws(static_cast<unsigned char>(piece.front()))) ? 1 : 0;
        if (utf8_chars(out.back()) + extra + utf8_chars(piece) <= later_limit)
            glue_text(out.back(), piece);
        else
            out.push_back(std::move(piece));
    }
    return out;
}

static int quiet_edge(const std::vector<float>& x, bool tail, float amp2) {
    const int n = static_cast<int>(x.size());
    int i = 0;
    while (i < n) {
        const float sample = tail ? x[n - 1 - i] : x[i];
        if (sample * sample >= amp2) break;
        ++i;
    }
    return i;
}

void glue(std::vector<float>& dst, const std::vector<float>& src, float quiet_amp2) {
    if (dst.empty()) {
        dst = src;
        log("event=glue mode=first samples=" + std::to_string(src.size()));
        return;
    }
    if (src.empty()) return;
    const int cap = std::min(kGlue, static_cast<int>(std::min(dst.size(), src.size())));
    int n = std::min(quiet_edge(dst, true, quiet_amp2), quiet_edge(src, false, quiet_amp2));
    n = std::min(cap, std::max(n, std::min(480, cap)));
    const float step = 1.5707963267948966f / static_cast<float>(std::max(n, 1));
    for (int i = 0; i < n; ++i) {
        const float w = static_cast<float>(i) * step;
        dst[dst.size() - n + i] = dst[dst.size() - n + i] * std::cos(w) + src[i] * std::sin(w);
    }
    dst.insert(dst.end(), src.begin() + n, src.end());
    log("event=glue mode=overlap overlap=" + std::to_string(n) + " src=" + std::to_string(src.size()) +
        " dst=" + std::to_string(dst.size()));
}

void write_wav(const std::string& path, const std::vector<float>& pcm) {
    if (pcm.empty()) throw std::runtime_error("cannot write empty WAV");
    if (pcm.size() > (std::numeric_limits<uint32_t>::max() - 36u) / 2u)
        throw std::runtime_error("WAV output is too large");
    std::vector<int16_t> samples(pcm.size());
    for (std::size_t i = 0; i < pcm.size(); ++i) {
        const float clipped = std::max(-1.0f, std::min(1.0f, pcm[i]));
        samples[i] = static_cast<int16_t>(clipped * 32767.0f);
    }
    const std::filesystem::path target(path);
    if (!target.parent_path().empty()) std::filesystem::create_directories(target.parent_path());
    std::ofstream out(target, std::ios::binary | std::ios::trunc);
    if (!out) throw std::runtime_error("cannot open WAV output: " + path);
    const uint32_t rate = static_cast<uint32_t>(kRate), data_size = static_cast<uint32_t>(samples.size() * 2u);
    const uint32_t riff_size = 36u + data_size, byte_rate = rate * 2u, fmt_size = 16u;
    const uint16_t format = 1u, channels = 1u, block_align = 2u, bits = 16u;
    auto put = [&](const auto& value) { out.write(reinterpret_cast<const char*>(&value), sizeof(value)); };
    out.write("RIFF", 4); put(riff_size); out.write("WAVEfmt ", 8);
    put(fmt_size); put(format); put(channels); put(rate); put(byte_rate); put(block_align); put(bits);
    out.write("data", 4); put(data_size);
    out.write(reinterpret_cast<const char*>(samples.data()), static_cast<std::streamsize>(data_size));
    if (!out) throw std::runtime_error("failed while writing WAV output: " + path);
    log("event=wav path=" + path + " samples=" + std::to_string(pcm.size()) +
        " seconds=" + std::to_string(pcm.size() / static_cast<double>(kRate)));
}

}
