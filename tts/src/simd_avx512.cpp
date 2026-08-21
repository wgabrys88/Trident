#include "simd.hpp"

#include <algorithm>
#include <immintrin.h>

namespace tts {

void pcm_f32_to_i16_avx512(const float* input, int16_t* output, std::size_t count) {
    const __m512 lo = _mm512_set1_ps(-1.0f);
    const __m512 hi = _mm512_set1_ps(1.0f);
    const __m512 scale = _mm512_set1_ps(32767.0f);
    std::size_t i = 0;
    for (; i + 16 <= count; i += 16) {
        __m512 v = _mm512_loadu_ps(input + i);
        v = _mm512_max_ps(lo, _mm512_min_ps(hi, v));
        const __m512i i32 = _mm512_cvttps_epi32(_mm512_mul_ps(v, scale));
        const __m256i i16 = _mm512_cvtsepi32_epi16(i32);
        _mm256_storeu_si256(reinterpret_cast<__m256i*>(output + i), i16);
    }
    for (; i < count; ++i) {
        const float clipped = std::max(-1.0f, std::min(1.0f, input[i]));
        output[i] = static_cast<int16_t>(clipped * 32767.0f);
    }
}

}
