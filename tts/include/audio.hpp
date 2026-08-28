#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace tts {

using StreamingSink = std::function<void(const float* pcm, std::size_t samples, int chunk_index, bool is_last)>;

inline constexpr int kRate = 24000;
inline constexpr int kGlue = 480;
inline constexpr float kQuietAmp2 = 0.0004f;

std::vector<std::int16_t> pcm16(const float* pcm, std::size_t count);
void write_wav(const std::string& path, const std::vector<float>& pcm);
std::vector<std::string> pack_text(const std::string& text, int limit);
std::vector<std::string> pack_text_staged(const std::string& text, int first_limit, int later_limit);
void glue(std::vector<float>& dst, const std::vector<float>& src, float quiet_amp2);
int utf8_chars(const std::string& s);

}
