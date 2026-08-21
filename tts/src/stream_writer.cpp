#include "stream_writer.hpp"

#include "audio.hpp"
#include "cli.hpp"
#include "simd.hpp"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <thread>
#include <vector>

namespace tts {
namespace {

void write_header(std::ofstream& out, std::uint32_t samples) {
    const std::uint32_t data_size = samples * 2u;
    const std::uint32_t riff_size = 36u + data_size;
    const std::uint32_t rate = static_cast<std::uint32_t>(kRate);
    const std::uint32_t byte_rate = rate * 2u;
    const std::uint32_t fmt_size = 16u;
    const std::uint16_t format = 1u, channels = 1u, block_align = 2u, bits = 16u;
    auto put = [&](const auto& value) { out.write(reinterpret_cast<const char*>(&value), sizeof(value)); };
    out.write("RIFF", 4); put(riff_size); out.write("WAVEfmt ", 8);
    put(fmt_size); put(format); put(channels); put(rate); put(byte_rate); put(block_align); put(bits);
    out.write("data", 4); put(data_size);
}

} // namespace

struct StreamingWavWriter::Impl {
    explicit Impl(std::string output_path, std::size_t capacity_samples)
        : path(std::move(output_path)), ring(std::max<std::size_t>(capacity_samples, 4096)) {
        const std::filesystem::path target(path);
        if (!target.parent_path().empty()) std::filesystem::create_directories(target.parent_path());
        out.open(target, std::ios::binary | std::ios::trunc);
        if (!out) throw std::runtime_error("cannot open streaming WAV output: " + path);
        write_header(out, 0);
        if (!out) throw std::runtime_error("cannot initialize streaming WAV output: " + path);
        log("stream ring=spsc capacity_samples=" + std::to_string(ring.size()) + " simd=" + pcm_simd_backend());
        consumer = std::thread([this] { consume(); });
    }

    ~Impl() {
        if (!finished) {
            done.store(true, std::memory_order_release);
            if (consumer.joinable()) consumer.join();
            out.close();
            std::error_code ec;
            std::filesystem::remove(path, ec);
        }
    }

    void push(const float* samples, std::size_t count) {
        if (!samples && count) throw std::invalid_argument("stream push received null samples");
        std::size_t offset = 0;
        while (offset < count) {
            if (failed.load(std::memory_order_acquire))
                throw std::runtime_error("streaming WAV consumer failed");
            const std::uint64_t h = head.load(std::memory_order_relaxed);
            const std::uint64_t t = tail.load(std::memory_order_acquire);
            const std::size_t used = static_cast<std::size_t>(h - t);
            const std::size_t free = ring.size() - used;
            if (free == 0) {
                std::this_thread::yield();
                continue;
            }
            const std::size_t index = static_cast<std::size_t>(h % ring.size());
            const std::size_t n = std::min({count - offset, free, ring.size() - index});
            std::copy_n(samples + offset, n, ring.data() + index);
            head.store(h + n, std::memory_order_release);
            offset += n;
        }
    }

    void finish() {
        if (finished) return;
        done.store(true, std::memory_order_release);
        if (consumer.joinable()) consumer.join();
        if (error) {
            out.close();
            std::error_code ec;
            std::filesystem::remove(path, ec);
            std::rethrow_exception(error);
        }
        if (samples_written > (std::numeric_limits<std::uint32_t>::max() - 36u) / 2u)
            throw std::runtime_error("WAV output is too large");
        out.seekp(0, std::ios::beg);
        write_header(out, static_cast<std::uint32_t>(samples_written));
        out.flush();
        if (!out) throw std::runtime_error("failed finalizing streaming WAV output: " + path);
        out.close();
        finished = true;
        log("wav path=" + path + " samples=" + std::to_string(samples_written) +
            " seconds=" + std::to_string(samples_written / static_cast<double>(kRate)) +
            " streaming=1 simd=" + pcm_simd_backend());
    }

    void consume() noexcept {
        try {
            std::vector<std::int16_t> converted(16384);
            for (;;) {
                const std::uint64_t t = tail.load(std::memory_order_relaxed);
                const std::uint64_t h = head.load(std::memory_order_acquire);
                const std::size_t available = static_cast<std::size_t>(h - t);
                if (available == 0) {
                    if (done.load(std::memory_order_acquire)) break;
                    std::this_thread::yield();
                    continue;
                }
                const std::size_t index = static_cast<std::size_t>(t % ring.size());
                const std::size_t n = std::min({available, ring.size() - index, converted.size()});
                if (samples_written + n > (std::numeric_limits<std::uint32_t>::max() - 36u) / 2u)
                    throw std::runtime_error("WAV output is too large");
                pcm_f32_to_i16(ring.data() + index, converted.data(), n);
                out.write(reinterpret_cast<const char*>(converted.data()), static_cast<std::streamsize>(n * sizeof(std::int16_t)));
                if (!out) throw std::runtime_error("failed writing streaming WAV output: " + path);
                samples_written += n;
                tail.store(t + n, std::memory_order_release);
            }
        } catch (...) {
            error = std::current_exception();
            failed.store(true, std::memory_order_release);
        }
    }

    std::string path;
    std::vector<float> ring;
    std::atomic<std::uint64_t> head{0};
    std::atomic<std::uint64_t> tail{0};
    std::atomic<bool> done{false};
    std::atomic<bool> failed{false};
    std::ofstream out;
    std::thread consumer;
    std::exception_ptr error;
    std::uint64_t samples_written = 0;
    bool finished = false;
};

StreamingWavWriter::StreamingWavWriter(const std::string& path, std::size_t capacity_samples)
    : impl_(std::make_unique<Impl>(path, capacity_samples)) {}
StreamingWavWriter::~StreamingWavWriter() = default;
void StreamingWavWriter::push(const float* samples, std::size_t count) { impl_->push(samples, count); }
void StreamingWavWriter::finish() { impl_->finish(); }

}
