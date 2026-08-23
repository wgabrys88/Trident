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

#ifdef _WIN32
#  include <winsock2.h>
#  include <ws2tcpip.h>
using socket_t = SOCKET;
static constexpr socket_t kInvalidSocket = INVALID_SOCKET;
#else
#  include <arpa/inet.h>
#  include <netinet/in.h>
#  include <sys/socket.h>
#  include <unistd.h>
using socket_t = int;
static constexpr socket_t kInvalidSocket = -1;
#endif

namespace {

const std::vector<std::string> kRequired = {
    "--family", "--model", "--s3gen-gguf", "--reference", "--language", "--port",
    "--n-gpu-layers", "--context", "--threads", "--seed", "--max-tokens",
    "--top-k", "--top-p", "--min-p", "--temperature", "--repeat-penalty", "--cfg-weight",
    "--exaggeration", "--cfm-steps", "--first-chunk-chars", "--chunk-chars",
};

void close_socket(socket_t sock) noexcept {
    if (sock == kInvalidSocket) return;
#ifdef _WIN32
    closesocket(sock);
#else
    close(sock);
#endif
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
#ifdef _WIN32
        const int got = recv(sock, reinterpret_cast<char*>(out + done), want, 0);
#else
        const int got = static_cast<int>(recv(sock, out + done, static_cast<std::size_t>(want), 0));
#endif
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
#ifdef _WIN32
        const int sent = send(sock, reinterpret_cast<const char*>(in + done), want, 0);
#else
        const int sent = static_cast<int>(send(sock, in + done, static_cast<std::size_t>(want), 0));
#endif
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

void send_response(socket_t sock, std::uint32_t status, const std::string& message) {
    if (message.size() > std::numeric_limits<std::uint32_t>::max())
        throw std::runtime_error("resident TTS response too large");
    const auto header = response_header(status, static_cast<std::uint32_t>(message.size()));
    send_all(sock, header.data(), header.size());
    if (!message.empty()) send_all(sock, message.data(), message.size());
}

void validate_knobs(const std::string& family, tts::EngineKnobs& knobs, int first_chunk_chars, int chunk_chars) {
    if (knobs.max_tokens < 1 || knobs.top_k < 0 || knobs.cfm_steps < 1 || first_chunk_chars < 1 || chunk_chars < 1)
        throw std::invalid_argument("integer sampling values are out of range");
    if (knobs.top_p < 0 || knobs.top_p > 1 || knobs.min_p < 0 || knobs.min_p > 1 || knobs.temperature < 0 ||
        knobs.repeat_penalty <= 0 || knobs.cfg_weight < 0 || knobs.exaggeration < 0)
        throw std::invalid_argument("sampling or voice values are out of range");
    if (family == "turbo" || family == "nano") {
        if (knobs.language != "en") throw std::invalid_argument("Turbo/Nano resident TTS supports language en only");
        knobs.min_p = 0.0f;
        knobs.cfg_weight = 0.0f;
        knobs.exaggeration = 0.0f;
    } else if (family == "v3") {
        if (knobs.language.size() != 2) throw std::invalid_argument("v3 resident TTS language must be a 2-letter code");
        if (knobs.cfm_steps < 5)
            throw std::invalid_argument("v3 CFM below 5 is unsupported by this quality-preserving runtime; use 5 or more");
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
                 " reference=" + knobs.reference);


        tts::Session session(runtime, knobs, chunk_chars, tts::kQuietAmp2, first_chunk_chars);

#ifdef _WIN32
        WSADATA wsa{};
        if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) throw std::runtime_error("WSAStartup failed");
        struct WsaGuard { ~WsaGuard() { WSACleanup(); } } wsa_guard;
#endif
        SocketGuard listener(socket(AF_INET, SOCK_STREAM, IPPROTO_TCP));
        if (listener.value == kInvalidSocket) throw std::runtime_error("resident TTS socket creation failed");
        int reuse = 1;
#ifdef _WIN32
        setsockopt(listener.value, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&reuse), sizeof(reuse));
#else
        setsockopt(listener.value, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
#endif
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
            std::array<unsigned char, 8> header{};

            if (!recv_all(client.value, header.data(), header.size())) continue;
            const std::uint32_t text_len = decode_u32_le(header.data());
            const std::uint32_t path_len = decode_u32_le(header.data() + 4);
            if (text_len == 0 || text_len > 4u * 1024u * 1024u || path_len == 0 || path_len > 32768u) {
                send_response(client.value, 1, "invalid request lengths");
                continue;
            }
            std::string text(text_len, '\0');
            std::string output(path_len, '\0');
            if (!recv_all(client.value, text.data(), text.size()) || !recv_all(client.value, output.data(), output.size()))
                continue;

            try {
                const std::uint64_t request_id = ++request_seq;
                tts::set_request_id(request_id);
                tts::log("event=request_start text_bytes=" + std::to_string(text.size()) + " output=" + output);
                const auto started = std::chrono::steady_clock::now();
                const tts::Speech speech = session.synthesize(text);
                tts::write_wav(output, speech.pcm);
                const double total_ms = std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - started).count();
                tts::print_done(speech, total_ms, session.runtime(), session.knobs(), session.chunk_chars());
                const double audio_ms = speech.pcm.size() * 1000.0 / tts::kRate;
                const double wall_rtf = audio_ms > 0 ? total_ms / audio_ms : 0.0;
                const std::string result = "request_id=" + std::to_string(request_id) +
                    " samples=" + std::to_string(speech.pcm.size()) +
                    " chunks=" + std::to_string(speech.chunks) +
                    " t3_ms=" + std::to_string(speech.t3_ms) +
                    " s3gen_ms=" + std::to_string(speech.s3gen_ms) +
                    " ttfa_ms=" + std::to_string(speech.ttfa_ms) +
                    " total_ms=" + std::to_string(total_ms) +
                    " wall_rtf=" + std::to_string(wall_rtf) +
                    " ttfa_scope=server-internal-whole-s3gen-chunk client_streaming=0";
                send_response(client.value, 0, result);
            } catch (const std::exception& error) {
                tts::log(std::string("event=request_error message=") + error.what());
                send_response(client.value, 1, error.what());
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
