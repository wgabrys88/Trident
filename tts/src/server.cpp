#include "audio.hpp"
#include "cli.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <winsock2.h>
#include <ws2tcpip.h>
#include "tts-cpp/chatterbox/engine.h"

using socket_t = SOCKET;
static constexpr socket_t kInvalidSocket = INVALID_SOCKET;

namespace {

const std::vector<std::string> kRequired = {
    "--family", "--model", "--s3gen-gguf", "--reference", "--language", "--port",
    "--n-gpu-layers", "--context", "--threads", "--seed", "--max-tokens",
    "--top-k", "--top-p", "--min-p", "--temperature", "--repeat-penalty", "--cfg-weight",
    "--exaggeration", "--cfm-steps", "--fastconv",
};

constexpr std::uint32_t kKindPcm   = 2;
constexpr std::uint32_t kKindDone  = 0;
constexpr std::uint32_t kKindError = 1;

void close_socket(socket_t sock) noexcept {
    if (sock == kInvalidSocket) return;
    closesocket(sock);
}

struct SocketGuard {
    socket_t value = kInvalidSocket;
    explicit SocketGuard(socket_t sock = kInvalidSocket) : value(sock) {}
    ~SocketGuard() { close_socket(value); }
    SocketGuard(const SocketGuard&) = delete;
    SocketGuard& operator=(const SocketGuard&) = delete;
};

bool recv_all(socket_t sock, void* dst, std::size_t size) {
    auto* out = static_cast<unsigned char*>(dst);
    std::size_t done = 0;
    while (done < size) {
        const std::size_t left = size - done;
        const int want = static_cast<int>(std::min<std::size_t>(left, static_cast<std::size_t>(std::numeric_limits<int>::max())));
        const int got = recv(sock, reinterpret_cast<char*>(out + done), want, 0);
        if (got <= 0) return false;
        done += static_cast<std::size_t>(got);
    }
    return true;
}

void send_all(socket_t sock, const void* src, std::size_t size) {
    const auto* in = static_cast<const unsigned char*>(src);
    std::size_t done = 0;
    while (done < size) {
        const std::size_t left = size - done;
        const int want = static_cast<int>(std::min<std::size_t>(left, static_cast<std::size_t>(std::numeric_limits<int>::max())));
        const int sent = send(sock, reinterpret_cast<const char*>(in + done), want, 0);
        if (sent <= 0) throw std::runtime_error("resident TTS socket send failed");
        done += static_cast<std::size_t>(sent);
    }
}

std::uint32_t decode_u32_le(const unsigned char* p) {
    return static_cast<std::uint32_t>(p[0]) |
           (static_cast<std::uint32_t>(p[1]) << 8u) |
           (static_cast<std::uint32_t>(p[2]) << 16u) |
           (static_cast<std::uint32_t>(p[3]) << 24u);
}

std::array<unsigned char, 8> response_header(std::uint32_t kind, std::uint32_t bytes) {
    std::array<unsigned char, 8> out{};
    const std::uint32_t values[2] = {kind, bytes};
    for (int j = 0; j < 2; ++j) {
        const std::uint32_t v = values[j];
        out[4 * j + 0] = static_cast<unsigned char>(v & 0xffu);
        out[4 * j + 1] = static_cast<unsigned char>((v >> 8u) & 0xffu);
        out[4 * j + 2] = static_cast<unsigned char>((v >> 16u) & 0xffu);
        out[4 * j + 3] = static_cast<unsigned char>((v >> 24u) & 0xffu);
    }
    return out;
}

void send_frame(socket_t sock, std::uint32_t kind, const void* data, std::size_t size) {
    if (size > std::numeric_limits<std::uint32_t>::max())
        throw std::runtime_error("resident TTS frame too large");
    const auto header = response_header(kind, static_cast<std::uint32_t>(size));
    send_all(sock, header.data(), header.size());
    if (size) send_all(sock, data, size);
}

void send_pcm(socket_t sock, const float* pcm, std::size_t count) {
    const auto samples = tts::pcm16(pcm, count);
    send_frame(sock, kKindPcm, samples.data(), samples.size() * sizeof(samples[0]));
}

void validate_knobs(const std::string& family, const tts::EngineKnobs& knobs) {
    if (knobs.max_tokens < 1 || knobs.top_k < 0 || knobs.cfm_steps < 1)
        throw std::invalid_argument("integer sampling values are out of range");
    if (knobs.top_p < 0 || knobs.top_p > 1 || knobs.min_p < 0 || knobs.min_p > 1 || knobs.temperature < 0 ||
        knobs.repeat_penalty <= 0 || knobs.cfg_weight < 0 || knobs.exaggeration < 0)
        throw std::invalid_argument("sampling or voice values are out of range");
    if (family != "nano") throw std::invalid_argument("unknown resident TTS family: " + family);
    if (knobs.language != "en") throw std::invalid_argument("Nano resident TTS supports language en only");
    if (knobs.min_p != 0.0f || knobs.cfg_weight != 0.0f || knobs.exaggeration != 0.0f)
        throw std::invalid_argument("Nano does not support min-p, CFG weight, or exaggeration");
}

void watch_client_hangup(socket_t sock, tts_cpp::chatterbox::Engine& engine, std::atomic<bool>& done) {
    while (!done.load(std::memory_order_relaxed)) {
        fd_set readfds;
        FD_ZERO(&readfds);
        FD_SET(sock, &readfds);
        timeval tv{};
        tv.tv_sec = 0;
        tv.tv_usec = 50000;
        const int n = select(0, &readfds, nullptr, nullptr, &tv);
        if (n == SOCKET_ERROR) {
            engine.cancel();
            return;
        }
        if (n > 0 && FD_ISSET(sock, &readfds)) {
            char b = 0;
            const int got = recv(sock, &b, 1, MSG_PEEK);
            if (got == 0 || got == SOCKET_ERROR) {
                engine.cancel();
                return;
            }
        }
    }
}

tts_cpp::chatterbox::Engine make_engine(const std::string& t3_gguf, const std::string& s3gen_gguf,
                                       const tts::Runtime& runtime, const tts::EngineKnobs& knobs) {
    tts_cpp::chatterbox::EngineOptions opts;
    opts.t3_gguf_path = t3_gguf;
    opts.s3gen_gguf_path = s3gen_gguf;
    opts.reference_audio = knobs.reference;
    opts.n_gpu_layers = runtime.n_gpu_layers;
    opts.n_threads = runtime.threads;
    opts.seed = knobs.seed;
    opts.n_predict = knobs.max_tokens;
    opts.n_ctx = runtime.context;
    opts.top_k = knobs.top_k;
    opts.top_p = knobs.top_p;
    opts.temperature = knobs.temperature;
    opts.repeat_penalty = knobs.repeat_penalty;
    opts.language = knobs.language;
    opts.exaggeration = knobs.exaggeration;
    opts.cfg_weight = knobs.cfg_weight;
    opts.min_p = knobs.min_p;
    opts.cfm_steps = knobs.cfm_steps;
    opts.fastconv = runtime.fastconv != 0;
    return tts_cpp::chatterbox::Engine(opts);
}

}

int main(int argc, char** argv) {
    try {
        const tts::Args args = tts::parse_args(argc, argv, kRequired);
        const std::string family = args.at("--family");
        const int port = tts::parse_int(args, "--port");
        if (port < 1 || port > 65535) throw std::invalid_argument("--port is out of range");

        tts::EngineKnobs knobs;
        knobs.reference = args.at("--reference");
        knobs.language = args.at("--language");
        knobs.seed = tts::parse_int(args, "--seed");
        knobs.max_tokens = tts::parse_int(args, "--max-tokens");
        knobs.top_k = tts::parse_int(args, "--top-k");
        knobs.top_p = tts::parse_float(args, "--top-p");
        knobs.min_p = tts::parse_float(args, "--min-p");
        knobs.temperature = tts::parse_float(args, "--temperature");
        knobs.repeat_penalty = tts::parse_float(args, "--repeat-penalty");
        knobs.cfg_weight = tts::parse_float(args, "--cfg-weight");
        knobs.exaggeration = tts::parse_float(args, "--exaggeration");
        knobs.cfm_steps = tts::parse_int(args, "--cfm-steps");
        validate_knobs(family, knobs);

        const tts::Runtime runtime = tts::runtime_from(args);
        tts::log("event=resident_start family=" + family + " preload=begin language=" + knobs.language);

        tts_cpp::chatterbox::Engine engine = make_engine(args.at("--model"), args.at("--s3gen-gguf"), runtime, knobs);

        WSADATA wsa{};
        if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) throw std::runtime_error("WSAStartup failed");
        struct WsaGuard { ~WsaGuard() { WSACleanup(); } } wsa_guard;
        SocketGuard listener(socket(AF_INET, SOCK_STREAM, IPPROTO_TCP));
        if (listener.value == kInvalidSocket) throw std::runtime_error("resident TTS socket creation failed");
        int reuse = 1;
        setsockopt(listener.value, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&reuse), sizeof(reuse));
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_port = htons(static_cast<unsigned short>(port));
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(listener.value, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0)
            throw std::runtime_error("resident TTS bind failed on 127.0.0.1:" + std::to_string(port));
        if (listen(listener.value, 8) != 0) throw std::runtime_error("resident TTS listen failed");
        tts::log("event=resident_ready ready=1 family=" + family + " host=127.0.0.1 port=" + std::to_string(port) +
                 " model_resident=1 reference_conditioning_resident=1 language=" + knobs.language);

        std::uint64_t request_seq = 0;
        for (;;) {
            SocketGuard client(accept(listener.value, nullptr, nullptr));
            if (client.value == kInvalidSocket) continue;

            const std::uint64_t request_id = ++request_seq;
            bool request_failed = false;
            std::string request_error;

            try {
                std::array<unsigned char, 4> header{};
                if (!recv_all(client.value, header.data(), header.size())) continue;
                const std::uint32_t piece_count = decode_u32_le(header.data());
                if (piece_count == 0 || piece_count > 4096u) {
                    request_error = "piece_count out of range";
                    request_failed = true;
                } else {
                    std::vector<std::string> texts;
                    texts.reserve(piece_count);
                    for (std::uint32_t i = 0; i < piece_count; ++i) {
                        std::array<unsigned char, 4> piece_header{};
                        if (!recv_all(client.value, piece_header.data(), piece_header.size())) {
                            request_error = "short piece header";
                            request_failed = true;
                            break;
                        }
                        const std::uint32_t tlen = decode_u32_le(piece_header.data());
                        if (tlen == 0 || tlen > 4u * 1024u * 1024u) {
                            request_error = "piece text too large";
                            request_failed = true;
                            break;
                        }
                        std::string text(tlen, '\0');
                        if (!recv_all(client.value, text.data(), text.size())) {
                            request_error = "short text read";
                            request_failed = true;
                            break;
                        }
                        texts.push_back(std::move(text));
                    }
                    if (!request_failed) {
                        const auto started_overall = std::chrono::steady_clock::now();
                        bool first_audio_seen = false;
                        auto mark_first = [&]() {
                            if (first_audio_seen) return;
                            first_audio_seen = true;
                            tts::log("event=first_audio request_id=" + std::to_string(request_id));
                        };
                        std::atomic<bool> finished{false};
                        std::thread hangup([&] { watch_client_hangup(client.value, engine, finished); });
                        std::vector<tts_cpp::chatterbox::Engine::PieceResult> results;
                        try {
                            results = engine.synthesize_pieces(texts,
                                [&](int, const float* pcm, std::size_t count, int, bool) {
                                    if (count == 0) return;
                                    mark_first();
                                    send_pcm(client.value, pcm, count);
                                });
                        } catch (...) {
                            finished.store(true, std::memory_order_relaxed);
                            engine.cancel();
                            hangup.join();
                            throw;
                        }
                        finished.store(true, std::memory_order_relaxed);
                        hangup.join();
                        std::uint64_t total_t3_ms = 0;
                        std::uint64_t total_s3gen_ms = 0;
                        std::uint64_t total_t3_tokens = 0;
                        std::uint64_t samples = 0;
                        for (auto& r : results) {
                            total_t3_ms += static_cast<std::uint64_t>(r.t3_ms);
                            total_s3gen_ms += static_cast<std::uint64_t>(r.s3gen_ms);
                            total_t3_tokens += static_cast<std::uint64_t>(r.t3_tokens);
                            samples += r.pcm.size();
                        }
                        const double total_ms = std::chrono::duration<double, std::milli>(
                            std::chrono::steady_clock::now() - started_overall).count();
                        const double audio_ms = samples * 1000.0 / tts::kRate;
                        const double wall_rtf = audio_ms > 0 ? total_ms / audio_ms : 0.0;
                        const std::string result = "request_id=" + std::to_string(request_id) +
                            " samples=" + std::to_string(samples) +
                            " pieces=" + std::to_string(results.size()) +
                            " t3_ms=" + std::to_string(total_t3_ms) +
                            " s3gen_ms=" + std::to_string(total_s3gen_ms) +
                            " t3_tokens=" + std::to_string(total_t3_tokens) +
                            " total_ms=" + std::to_string(total_ms) +
                            " wall_rtf=" + std::to_string(wall_rtf);
                        try { send_frame(client.value, kKindDone, result.data(), result.size()); } catch (...) {}
                    }
                }
            } catch (const std::exception& exc) {
                engine.cancel();
                request_error = exc.what();
                request_failed = true;
            }

            if (request_failed) {
                try { send_frame(client.value, kKindError, request_error.data(), request_error.size()); } catch (...) {}
                tts::log("event=request_failed request_id=" + std::to_string(request_id) + " message=" + request_error);
            }
        }
    } catch (const std::invalid_argument& error) {
        std::cerr << "argument error: " << error.what() << std::endl;
        return 2;
    } catch (const std::exception& error) {
        std::cerr << "tts resident error: " << error.what() << std::endl;
        return 1;
    }
}
