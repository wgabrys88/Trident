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
#include <unordered_map>
#include <vector>

#include <winsock2.h>
#include <ws2tcpip.h>
#include "tts-cpp/chatterbox/engine.h"

using socket_t = SOCKET;
static constexpr socket_t kInvalid = INVALID_SOCKET;
static constexpr std::uint32_t kPcm = 2, kDone = 0, kErr = 1;

static void close_sock(socket_t s) noexcept {
    if (s != kInvalid) closesocket(s);
}

struct Sock {
    socket_t v = kInvalid;
    explicit Sock(socket_t s = kInvalid) : v(s) {}
    ~Sock() { close_sock(v); }
    Sock(const Sock&) = delete;
    Sock& operator=(const Sock&) = delete;
};

static bool recv_all(socket_t s, void* dst, std::size_t n) {
    auto* p = static_cast<unsigned char*>(dst);
    std::size_t done = 0;
    while (done < n) {
        const int got = recv(s, reinterpret_cast<char*>(p + done),
                             static_cast<int>(std::min<std::size_t>(n - done, 1 << 20)), 0);
        if (got <= 0) return false;
        done += static_cast<std::size_t>(got);
    }
    return true;
}

static void send_all(socket_t s, const void* src, std::size_t n) {
    const auto* p = static_cast<const unsigned char*>(src);
    std::size_t done = 0;
    while (done < n) {
        const int sent = send(s, reinterpret_cast<const char*>(p + done),
                              static_cast<int>(std::min<std::size_t>(n - done, 1 << 20)), 0);
        if (sent <= 0) throw std::runtime_error("TTS send failed");
        done += static_cast<std::size_t>(sent);
    }
}

static std::uint32_t u32le(const unsigned char* p) {
    return (std::uint32_t)p[0] | ((std::uint32_t)p[1] << 8) | ((std::uint32_t)p[2] << 16) | ((std::uint32_t)p[3] << 24);
}

static void send_frame(socket_t s, std::uint32_t kind, const void* data, std::size_t n) {
    unsigned char h[8];
    const std::uint32_t vals[2] = {kind, static_cast<std::uint32_t>(n)};
    for (int i = 0; i < 2; ++i) {
        h[4 * i + 0] = (unsigned char)(vals[i] & 0xff);
        h[4 * i + 1] = (unsigned char)((vals[i] >> 8) & 0xff);
        h[4 * i + 2] = (unsigned char)((vals[i] >> 16) & 0xff);
        h[4 * i + 3] = (unsigned char)((vals[i] >> 24) & 0xff);
    }
    send_all(s, h, 8);
    if (n) send_all(s, data, n);
}

static void send_pcm(socket_t s, const float* pcm, std::size_t count) {
    std::vector<std::int16_t> out(count);
    for (std::size_t i = 0; i < count; ++i) {
        const float c = std::max(-1.0f, std::min(1.0f, pcm[i]));
        out[i] = static_cast<std::int16_t>(c * 32767.0f);
    }
    send_frame(s, kPcm, out.data(), out.size() * 2);
}

static void watch_hangup(socket_t s, tts_cpp::chatterbox::Engine& engine, std::atomic<bool>& done) {
    while (!done.load(std::memory_order_relaxed)) {
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(s, &fds);
        timeval tv{0, 50000};
        const int n = select(0, &fds, nullptr, nullptr, &tv);
        if (n == SOCKET_ERROR) {
            engine.cancel();
            return;
        }
        if (n > 0 && FD_ISSET(s, &fds)) {
            char b = 0;
            const int got = recv(s, &b, 1, MSG_PEEK);
            if (got == 0 || got == SOCKET_ERROR) {
                engine.cancel();
                return;
            }
        }
    }
}

static tts_cpp::chatterbox::Engine make_engine(const std::unordered_map<std::string, std::string>& a) {
    tts_cpp::chatterbox::EngineOptions o;
    o.t3_gguf_path = a.at("--model");
    o.s3gen_gguf_path = a.at("--s3gen-gguf");
    o.reference_audio = a.at("--reference");
    o.n_gpu_layers = std::stoi(a.at("--n-gpu-layers"));
    o.n_threads = std::stoi(a.at("--threads"));
    o.seed = std::stoi(a.at("--seed"));
    o.n_predict = std::stoi(a.at("--max-tokens"));
    o.n_ctx = std::stoi(a.at("--context"));
    o.top_k = std::stoi(a.at("--top-k"));
    o.top_p = std::stof(a.at("--top-p"));
    o.temperature = std::stof(a.at("--temperature"));
    o.repeat_penalty = std::stof(a.at("--repeat-penalty"));
    o.language = a.at("--language");
    o.exaggeration = std::stof(a.at("--exaggeration"));
    o.cfg_weight = std::stof(a.at("--cfg-weight"));
    o.min_p = std::stof(a.at("--min-p"));
    o.cfm_steps = std::stoi(a.at("--cfm-steps"));
    o.fastconv = std::stoi(a.at("--fastconv")) != 0;
    if (a.at("--family") != "nano") throw std::invalid_argument("family must be nano");
    if (o.language != "en") throw std::invalid_argument("language must be en");
    if (o.min_p != 0.0f || o.cfg_weight != 0.0f || o.exaggeration != 0.0f)
        throw std::invalid_argument("nano does not support min-p, cfg, or exaggeration");
    return tts_cpp::chatterbox::Engine(o);
}

int main(int argc, char** argv) {
    try {
        std::unordered_map<std::string, std::string> args;
        for (int i = 1; i + 1 < argc; i += 2) args[argv[i]] = argv[i + 1];
        for (const char* k : {"--family", "--model", "--s3gen-gguf", "--reference", "--language", "--port",
                              "--n-gpu-layers", "--context", "--threads", "--seed", "--max-tokens",
                              "--top-k", "--top-p", "--min-p", "--temperature", "--repeat-penalty",
                              "--cfg-weight", "--exaggeration", "--cfm-steps", "--fastconv"}) {
            if (!args.count(k)) throw std::invalid_argument(std::string("missing ") + k);
        }
        const int port = std::stoi(args.at("--port"));
        tts_cpp::chatterbox::Engine engine = make_engine(args);

        WSADATA wsa{};
        if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) throw std::runtime_error("WSAStartup failed");
        struct Wsa { ~Wsa() { WSACleanup(); } } wsa_guard;
        Sock listener(socket(AF_INET, SOCK_STREAM, IPPROTO_TCP));
        if (listener.v == kInvalid) throw std::runtime_error("socket failed");
        int reuse = 1;
        setsockopt(listener.v, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&reuse), sizeof(reuse));
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(static_cast<unsigned short>(port));
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(listener.v, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0)
            throw std::runtime_error("bind 127.0.0.1:" + std::to_string(port) + " failed");
        if (listen(listener.v, 8) != 0) throw std::runtime_error("listen failed");
        std::cerr << "tts ready host=127.0.0.1 port=" << port << std::endl;

        std::uint64_t seq = 0;
        for (;;) {
            Sock client(accept(listener.v, nullptr, nullptr));
            if (client.v == kInvalid) continue;
            const auto id = ++seq;
            std::string err;
            try {
                unsigned char hdr[4];
                if (!recv_all(client.v, hdr, 4)) continue;
                const auto n = u32le(hdr);
                if (n == 0 || n > 4096u) throw std::runtime_error("piece_count out of range");
                std::vector<std::string> texts;
                texts.reserve(n);
                for (std::uint32_t i = 0; i < n; ++i) {
                    unsigned char th[4];
                    if (!recv_all(client.v, th, 4)) throw std::runtime_error("short piece header");
                    const auto len = u32le(th);
                    if (len == 0 || len > 4u * 1024u * 1024u) throw std::runtime_error("piece too large");
                    std::string text(len, '\0');
                    if (!recv_all(client.v, text.data(), text.size())) throw std::runtime_error("short text");
                    texts.push_back(std::move(text));
                }
                std::atomic<bool> finished{false};
                std::thread hangup([&] { watch_hangup(client.v, engine, finished); });
                try {
                    engine.synthesize_pieces(texts, [&](int, const float* pcm, std::size_t count, int, bool) {
                        if (count) send_pcm(client.v, pcm, count);
                    });
                } catch (...) {
                    finished.store(true);
                    engine.cancel();
                    hangup.join();
                    throw;
                }
                finished.store(true);
                hangup.join();
                const char* ok = "ok";
                try { send_frame(client.v, kDone, ok, 2); } catch (...) {}
            } catch (const std::exception& e) {
                engine.cancel();
                err = e.what();
                try { send_frame(client.v, kErr, err.data(), err.size()); } catch (...) {}
                std::cerr << "tts request " << id << " failed: " << err << std::endl;
            }
        }
    } catch (const std::exception& e) {
        std::cerr << "tts resident error: " << e.what() << std::endl;
        return 1;
    }
}
