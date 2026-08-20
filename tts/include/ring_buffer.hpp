#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace tts {

// Lock-free SPSC ring of 24 kHz float PCM. One synth thread pushes frames,
// the main / audio thread pops. Capacity is a power of two.
class RingBuffer {
public:
    explicit RingBuffer(size_t capacity_pow2 = 1 << 16) {
        if (capacity_pow2 < 1024 || (capacity_pow2 & (capacity_pow2 - 1)) != 0)
            throw std::invalid_argument("ring capacity must be a power of two");
        buf_.assign(capacity_pow2, 0.0f);
        mask_ = capacity_pow2 - 1;
    }

    size_t capacity() const { return buf_.size(); }
    size_t size() const {
        const size_t w = write_.load(std::memory_order_acquire);
        const size_t r = read_.load(std::memory_order_relaxed);
        return w - r;
    }
    size_t free() const { return buf_.size() - size(); }
    bool empty() const { return size() == 0; }

    // Producer. Returns frames actually written.
    size_t push(const float* src, size_t n) {
        const size_t cap = buf_.size();
        const size_t w = write_.load(std::memory_order_relaxed);
        const size_t r = read_.load(std::memory_order_acquire);
        const size_t room = cap - (w - r);
        const size_t take = n < room ? n : room;
        for (size_t i = 0; i < take; ++i) buf_[(w + i) & mask_] = src[i];
        write_.store(w + take, std::memory_order_release);
        pushed_ += take;
        dropped_ += n - take;
        return take;
    }

    size_t push(const std::vector<float>& src) {
        return push(src.data(), src.size());
    }

    // Consumer. Returns frames actually read.
    size_t pop(float* dst, size_t n) {
        const size_t w = write_.load(std::memory_order_acquire);
        const size_t r = read_.load(std::memory_order_relaxed);
        const size_t have = w - r;
        const size_t take = n < have ? n : have;
        for (size_t i = 0; i < take; ++i) dst[i] = buf_[(r + i) & mask_];
        read_.store(r + take, std::memory_order_release);
        popped_ += take;
        return take;
    }

    uint64_t pushed() const { return pushed_; }
    uint64_t popped() const { return popped_; }
    uint64_t dropped() const { return dropped_; }

    void reset() {
        write_.store(0, std::memory_order_relaxed);
        read_.store(0, std::memory_order_relaxed);
        pushed_ = popped_ = dropped_ = 0;
    }

private:
    std::vector<float> buf_;
    size_t mask_ = 0;
    alignas(64) std::atomic<size_t> write_{0};
    alignas(64) std::atomic<size_t> read_{0};
    uint64_t pushed_ = 0, popped_ = 0, dropped_ = 0;
};

} // namespace tts
