#include "simd.hpp"

#include <algorithm>

#if defined(_MSC_VER) && (defined(_M_X64) || defined(_M_AMD64))
#include <intrin.h>
#endif

namespace tts {

#if defined(TRIDENT_HAVE_AVX512_IMPL)
void pcm_f32_to_i16_avx512(const float* input, int16_t* output, std::size_t count);

static bool cpu_has_avx512f_bw() {
#if defined(_MSC_VER) && (defined(_M_X64) || defined(_M_AMD64))
    int regs[4] = {};
    __cpuid(regs, 0);
    if (regs[0] < 7) return false;
    __cpuidex(regs, 1, 0);
    const bool osxsave = (regs[2] & (1 << 27)) != 0;
    const bool avx = (regs[2] & (1 << 28)) != 0;
    if (!osxsave || !avx) return false;
    const unsigned long long xcr0 = _xgetbv(0);
    if ((xcr0 & 0xE6u) != 0xE6u) return false;
    __cpuidex(regs, 7, 0);
    const bool avx512f = (regs[1] & (1 << 16)) != 0;
    const bool avx512bw = (regs[1] & (1 << 30)) != 0;
    return avx512f && avx512bw;
#elif (defined(__GNUC__) || defined(__clang__)) && (defined(__x86_64__) || defined(__i386__))
    __builtin_cpu_init();
    return __builtin_cpu_supports("avx512f") && __builtin_cpu_supports("avx512bw");
#else
    return false;
#endif
}
#endif

const char* pcm_simd_backend() {
#if defined(TRIDENT_HAVE_AVX512_IMPL)
    static const bool have = cpu_has_avx512f_bw();
    return have ? "avx512" : "scalar";
#else
    return "scalar";
#endif
}

void pcm_f32_to_i16(const float* input, int16_t* output, std::size_t count) {
#if defined(TRIDENT_HAVE_AVX512_IMPL)
    static const bool have = cpu_has_avx512f_bw();
    if (have) {
        pcm_f32_to_i16_avx512(input, output, count);
        return;
    }
#endif
    for (std::size_t i = 0; i < count; ++i) {
        const float clipped = std::max(-1.0f, std::min(1.0f, input[i]));
        output[i] = static_cast<int16_t>(clipped * 32767.0f);
    }
}

}
