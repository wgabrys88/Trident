#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <deque>
#include <iostream>
#include <mutex>
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

static void put_u32(unsigned char* p, std::uint32_t v) {
    p[0] = (unsigned char)(v & 0xff);
    p[1] = (unsigned char)((v >> 8) & 0xff);
    p[2] = (unsigned char)((v >> 16) & 0xff);
    p[3] = (unsigned char)((v >> 24) & 0xff);
}

static std::uint32_t get_u32(const unsigned char* p) {
    return (std::uint32_t)p[0]
        | ((std::uint32_t)p[1] << 8)
        | ((std::uint32_t)p[2] << 16)
        | ((std::uint32_t)p[3] << 24);
}

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

static void send_frame(socket_t s, std::mutex& send_mu, std::uint32_t kind, std::uint32_t epoch,
                       const void* data, std::size_t n) {
    unsigned char h[12];
    put_u32(h, kind);
    put_u32(h + 4, epoch);
    put_u32(h + 8, static_cast<std::uint32_t>(n));
    std::lock_guard<std::mutex> lock(send_mu);
    send_all(s, h, 12);
    if (n) send_all(s, data, n);
}

static void send_pcm(socket_t s, std::mutex& send_mu, std::uint32_t epoch, const float* pcm, std::size_t count) {
    std::vector<std::int16_t> out(count);
    for (std::size_t i = 0; i < count; ++i) {
        const float c = std::max(-1.0f, std::min(1.0f, pcm[i]));
        out[i] = static_cast<std::int16_t>(c * 32767.0f);
    }
    send_frame(s, send_mu, kPcm, epoch, out.data(), out.size() * 2);
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

static bool recv_request(socket_t s, std::uint32_t& epoch, std::vector<std::string>& texts) {
    unsigned char hdr[8];
    if (!recv_all(s, hdr, 8)) return false;
    const std::uint32_t n = get_u32(hdr);
    epoch = get_u32(hdr + 4);
    if (n > 4096u) throw std::runtime_error("piece_count out of range");
    texts.clear();
    texts.reserve(n);
    for (std::uint32_t i = 0; i < n; ++i) {
        unsigned char th[4];
        if (!recv_all(s, th, 4)) throw std::runtime_error("short piece header");
        const std::uint32_t len = get_u32(th);
        if (len > 4u * 1024u * 1024u) throw std::runtime_error("piece too large");
        std::string text(len, '\0');
        if (!recv_all(s, text.data(), text.size())) throw std::runtime_error("short text");
        texts.push_back(std::move(text));
    }
    return true;
}

static void serve(socket_t client, tts_cpp::chatterbox::Engine& engine) {
    std::mutex mu;
    std::mutex send_mu;
    std::condition_variable cv;
    std::deque<std::string> queue;
    std::atomic<std::uint32_t> live_epoch{0};
    std::atomic<bool> stop{false};

    std::thread synth([&] {
        for (;;) {
            std::vector<std::string> batch;
            std::uint32_t ep = 0;
            {
                std::unique_lock<std::mutex> lock(mu);
                cv.wait(lock, [&] { return stop.load() || !queue.empty(); });
                if (stop.load() && queue.empty()) return;
                ep = live_epoch.load();
                while (!queue.empty()) {
                    batch.push_back(std::move(queue.front()));
                    queue.pop_front();
                }
            }
            if (batch.empty()) continue;
            std::cerr << "tts synth epoch=" << ep << " pieces=" << batch.size() << std::endl;
            try {
                engine.synthesize_pieces(batch, [&](int, const float* pcm, std::size_t count, int, bool) {
                    if (count && live_epoch.load() == ep) send_pcm(client, send_mu, ep, pcm, count);
                });
                if (live_epoch.load() == ep) {
                    const char* ok = "ok";
                    send_frame(client, send_mu, kDone, ep, ok, 2);
                    std::cerr << "tts batch done epoch=" << ep << std::endl;
                }
            } catch (const std::exception& e) {
                if (live_epoch.load() != ep) {
                    std::cerr << "tts cancelled epoch=" << ep << " live=" << live_epoch.load()
                              << " " << e.what() << std::endl;
                    continue;
                }
                const std::string m = e.what();
                std::cerr << "tts synth failed epoch=" << ep << " " << m << std::endl;
                send_frame(client, send_mu, kErr, ep, m.data(), m.size());
                return;
            }
        }
    });

    try {
        for (;;) {
            std::uint32_t epoch = 0;
            std::vector<std::string> texts;
            if (!recv_request(client, epoch, texts)) break;
            {
                std::lock_guard<std::mutex> lock(mu);
                if (epoch != live_epoch.load()) {
                    std::cerr << "tts cancel live=" << live_epoch.load() << " epoch=" << epoch
                              << " queued=" << queue.size() << std::endl;
                    engine.cancel();
                    queue.clear();
                    live_epoch.store(epoch);
                }
                for (auto& text : texts) {
                    if (!text.empty()) queue.push_back(std::move(text));
                }
            }
            cv.notify_all();
        }
    } catch (const std::exception& e) {
        std::cerr << "tts recv error: " << e.what() << std::endl;
        try {
            send_frame(client, send_mu, kErr, live_epoch.load(), e.what(), std::strlen(e.what()));
        } catch (const std::exception& send_err) {
            std::cerr << "tts recv error send failed: " << send_err.what() << std::endl;
        }
    }
    stop.store(true);
    cv.notify_all();
    synth.join();
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
        if (listen(listener.v, 1) != 0) throw std::runtime_error("listen failed");
        std::cerr << "tts ready host=127.0.0.1 port=" << port << std::endl;

        for (;;) {
            Sock client(accept(listener.v, nullptr, nullptr));
            if (client.v == kInvalid) throw std::runtime_error("accept failed");
            std::cerr << "tts client connected" << std::endl;
            serve(client.v, engine);
            std::cerr << "tts client gone" << std::endl;
        }
    } catch (const std::exception& e) {
        std::cerr << "tts resident error: " << e.what() << std::endl;
        return 1;
    }
}
