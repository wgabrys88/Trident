#include "cli.hpp"
#include "session.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

#include <winsock2.h>
#include <ws2tcpip.h>
using socket_t = SOCKET;
static constexpr socket_t kInvalidSocket = INVALID_SOCKET;

namespace {

const std::vector<std::string> kRequired = {
    "--family", "--model", "--s3gen-gguf", "--reference", "--language", "--port",
    "--n-gpu-layers", "--context", "--threads", "--seed", "--max-tokens",
    "--top-k", "--top-p", "--min-p", "--temperature", "--repeat-penalty", "--cfg-weight",
    "--exaggeration", "--cfm-steps", "--first-chunk-chars", "--chunk-chars", "--fastconv",
};

constexpr std::uint32_t kPieceEnd    = 0xFFFFFFFFu;
constexpr std::uint32_t kPieceCancel = 0xFFFFFFFEu;

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

std::array<unsigned char, 8> response_header(std::uint32_t status, std::uint32_t bytes) {
    std::array<unsigned char, 8> out{};
    const std::uint32_t values[2] = {status, bytes};
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

void send_response(socket_t sock, std::uint32_t kind, const std::string& message) {
    send_frame(sock, kind, message.data(), message.size());
}

void send_pcm(socket_t sock, const float* pcm, std::size_t count) {
    const auto samples = tts::pcm16(pcm, count);
    send_frame(sock, 2, samples.data(), samples.size() * sizeof(samples[0]));
}

void validate_knobs(const std::string& family, const tts::EngineKnobs& knobs, int first_chunk_chars, int chunk_chars) {
    if (knobs.max_tokens < 1 || knobs.top_k < 0 || knobs.cfm_steps < 1 || first_chunk_chars < 1 || chunk_chars < 1)
        throw std::invalid_argument("integer sampling values are out of range");
    if (knobs.top_p < 0 || knobs.top_p > 1 || knobs.min_p < 0 || knobs.min_p > 1 || knobs.temperature < 0 ||
        knobs.repeat_penalty <= 0 || knobs.cfg_weight < 0 || knobs.exaggeration < 0)
        throw std::invalid_argument("sampling or voice values are out of range");
    if (family == "turbo" || family == "nano") {
        if (knobs.language != "en") throw std::invalid_argument("Turbo/Nano resident TTS supports language en only");
        if (knobs.min_p != 0.0f || knobs.cfg_weight != 0.0f || knobs.exaggeration != 0.0f)
            throw std::invalid_argument("Turbo/Nano do not support min-p, CFG weight, or exaggeration");
    } else if (family == "v3") {
        if (knobs.language.size() != 2) throw std::invalid_argument("v3 resident TTS language must be a 2-letter code");
    } else {
        throw std::invalid_argument("unknown resident TTS family: " + family);
    }
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
        const int first_chunk_chars = tts::parse_int(args, "--first-chunk-chars");
        const int chunk_chars = tts::parse_int(args, "--chunk-chars");
        validate_knobs(family, knobs, first_chunk_chars, chunk_chars);

        const tts::Runtime runtime = tts::runtime_from(args);
        tts::log("event=resident_start family=" + family + " preload=begin language=" + knobs.language +
                 " stream_chunk_tokens=" + std::to_string(runtime.stream_chunk_tokens) +
                 " stream_first_chunk_tokens=" + std::to_string(runtime.stream_first_chunk_tokens) +
                 " stream_cfm_steps=" + std::to_string(runtime.stream_cfm_steps));

        tts::Session session(runtime, knobs, chunk_chars, tts::kQuietAmp2, first_chunk_chars);

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
                 " model_resident=1 reference_conditioning_resident=1 language=" + knobs.language +
                 " streaming=" + (runtime.stream_chunk_tokens > 0 ? "1" : "0"));

        std::uint64_t request_seq = 0;
        for (;;) {
            SocketGuard client(accept(listener.value, nullptr, nullptr));
            if (client.value == kInvalidSocket) continue;

            std::string wav_path;
            tts::Speech session_speech;
            session_speech.pcm.clear();
            auto started_overall = std::chrono::steady_clock::now();
            bool first_audio_seen = false;
            auto mark_first = [&]() {
                if (first_audio_seen) return;
                first_audio_seen = true;
                session_speech.ttfa_ms = std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - started_overall).count();
            };
            std::uint32_t piece_index = 0;
            int pieces_synthesized = 0;
            const std::uint64_t request_id = ++request_seq;
            tts::set_request_id(request_id);
            bool stream_failed = false;
            std::string stream_error;

            for (;;) {
                std::array<unsigned char, 12> header{};
                if (!recv_all(client.value, header.data(), header.size())) break;
                const std::uint32_t pi   = decode_u32_le(header.data());
                const std::uint32_t plen = decode_u32_le(header.data() + 4);
                const std::uint32_t tlen = decode_u32_le(header.data() + 8);
                if (pi == kPieceEnd) break;
                if (pi == kPieceCancel) {
                    session.request_cancel();
                    continue;
                }
                if (tlen == 0 || tlen > 4u * 1024u * 1024u || plen > 32768u) {
                    stream_failed = true;
                    stream_error = "invalid piece header";
                    break;
                }
                std::string text(tlen, '\0');
                if (!recv_all(client.value, text.data(), text.size())) {
                    stream_failed = true;
                    stream_error = "short text read";
                    break;
                }
                std::string path(plen, '\0');
                if (plen && !recv_all(client.value, path.data(), path.size())) {
                    stream_failed = true;
                    stream_error = "short path read";
                    break;
                }
                if (!wav_path.empty()) {
                    tts::log("event=path_ignored piece_index=" + std::to_string(pi) +
                             " path=" + path);
                } else if (!path.empty()) {
                    wav_path = path;
                }
                piece_index = pi;
                try {
                    const auto piece_started = std::chrono::steady_clock::now();
                    const tts::StreamingSink sink = [&](const float* pcm, std::size_t count, int, bool) {
                        if (count == 0) return;
                        mark_first();
                        send_pcm(client.value, pcm, count);
                    };
                    tts::Speech piece_speech = session.synthesize_stream(text, sink);
                    session_speech.t3_ms    += piece_speech.t3_ms;
                    session_speech.s3gen_ms += piece_speech.s3gen_ms;
                    session_speech.t3_tokens += piece_speech.t3_tokens;
                    session_speech.pcm.insert(session_speech.pcm.end(), piece_speech.pcm.begin(), piece_speech.pcm.end());
                    pieces_synthesized += 1;
                    send_frame(client.value, 3, nullptr, 0);
                    const double piece_ms = std::chrono::duration<double, std::milli>(
                        std::chrono::steady_clock::now() - piece_started).count();
                    tts::log("event=piece_complete piece_index=" + std::to_string(pi) +
                             " pieces=" + std::to_string(pieces_synthesized) +
                             " t3_ms=" + std::to_string(piece_speech.t3_ms) +
                             " s3gen_ms=" + std::to_string(piece_speech.s3gen_ms) +
                             " t3_tokens=" + std::to_string(piece_speech.t3_tokens) +
                             " piece_ms=" + std::to_string(piece_ms));
                } catch (const std::exception& piece_error) {
                    stream_failed = true;
                    stream_error = piece_error.what();
                    break;
                }
            }

            if (stream_failed) {
                try { send_response(client.value, 1, stream_error); } catch (...) {}
                tts::log("event=stream_failed request_id=" + std::to_string(request_id) +
                         " message=" + stream_error);
                continue;
            }

            if (!wav_path.empty() && !session_speech.pcm.empty()) {
                try { tts::write_wav(wav_path, session_speech.pcm); }
                catch (const std::exception& wav_error) {
                    try { send_response(client.value, 1, std::string("wav write failed: ") + wav_error.what()); } catch (...) {}
                    continue;
                }
            }

            session_speech.chunks = pieces_synthesized;
            const double total_ms = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - started_overall).count();
            const double audio_ms = session_speech.pcm.size() * 1000.0 / tts::kRate;
            const double wall_rtf = audio_ms > 0 ? total_ms / audio_ms : 0.0;
            const std::string result = "request_id=" + std::to_string(request_id) +
                " samples=" + std::to_string(session_speech.pcm.size()) +
                " pieces=" + std::to_string(pieces_synthesized) +
                " t3_ms=" + std::to_string(session_speech.t3_ms) +
                " s3gen_ms=" + std::to_string(session_speech.s3gen_ms) +
                " ttfa_ms=" + std::to_string(session_speech.ttfa_ms) +
                " total_ms=" + std::to_string(total_ms) +
                " wall_rtf=" + std::to_string(wall_rtf);
            try { send_response(client.value, 0, result); } catch (...) {}
        }
    } catch (const std::invalid_argument& error) {
        std::cerr << "argument error: " << error.what() << std::endl;
        return 2;
    } catch (const std::exception& error) {
        std::cerr << "tts resident error: " << error.what() << std::endl;
        return 1;
    }
}
