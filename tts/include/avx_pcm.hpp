#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

#if defined(__AVX512F__)
#include <immintrin.h>
#endif

namespace tts {

// Float PCM [-1,1] -> int16, AVX-512 when compiled with /arch:AVX512 or -mavx512f.
inline void pcm_f32_to_s16(const float* src, int16_t* dst, size_t n) {
    size_t i = 0;
#if defined(__AVX512F__)
    const __m512 scale = _mm512_set1_ps(32767.0f);
    const __m512 lo = _mm512_set1_ps(-1.0f);
    const __m512 hi = _mm512_set1_ps(1.0f);
    for (; i + 16 <= n; i += 16) {
        __m512 v = _mm512_loadu_ps(src + i);
        v = _mm512_min_ps(_mm512_max_ps(v, lo), hi);
        v = _mm512_mul_ps(v, scale);
        __m512i as_i32 = _mm512_cvtps_epi32(v);
        __m256i packed = _mm512_cvtepi32_epi16(as_i32);
        _mm256_storeu_si256(reinterpret_cast<__m256i*>(dst + i), packed);
    }
#endif
    for (; i < n; ++i) {
        const float clipped = std::max(-1.0f, std::min(1.0f, src[i]));
        dst[i] = static_cast<int16_t>(clipped * 32767.0f);
    }
}

// Equal-power overlap-add used at pack joins. Vectorized cos/sin via scalar
// weights precomputed; the inner mix is AVX-512 FMA when available.
inline void glue_mix(float* dst_tail, const float* src_head, int n) {
    if (n <= 0) return;
    const float step = 1.5707963267948966f / static_cast<float>(n);
    int i = 0;
#if defined(__AVX512F__)
    for (; i + 16 <= n; i += 16) {
        alignas(64) float wcos[16], wsin[16];
        for (int k = 0; k < 16; ++k) {
            const float w = static_cast<float>(i + k) * step;
            wcos[k] = std::cos(w);
            wsin[k] = std::sin(w);
        }
        __m512 d = _mm512_loadu_ps(dst_tail + i);
        __m512 s = _mm512_loadu_ps(src_head + i);
        __m512 c = _mm512_load_ps(wcos);
        __m512 si = _mm512_load_ps(wsin);
        __m512 out = _mm512_fmadd_ps(s, si, _mm512_mul_ps(d, c));
        _mm512_storeu_ps(dst_tail + i, out);
    }
#endif
    for (; i < n; ++i) {
        const float w = static_cast<float>(i) * step;
        dst_tail[i] = dst_tail[i] * std::cos(w) + src_head[i] * std::sin(w);
    }
}

inline float peak_abs(const float* src, size_t n) {
    float peak = 0;
    size_t i = 0;
#if defined(__AVX512F__)
    __m512 acc = _mm512_setzero_ps();
    const __m512 sign = _mm512_castsi512_ps(_mm512_set1_epi32(0x7fffffff));
    for (; i + 16 <= n; i += 16) {
        __m512 v = _mm512_and_ps(_mm512_loadu_ps(src + i), sign);
        acc = _mm512_max_ps(acc, v);
    }
    alignas(64) float tmp[16];
    _mm512_store_ps(tmp, acc);
    for (int k = 0; k < 16; ++k) peak = std::max(peak, tmp[k]);
#endif
    for (; i < n; ++i) peak = std::max(peak, std::abs(src[i]));
    return peak;
}

} // namespace tts
