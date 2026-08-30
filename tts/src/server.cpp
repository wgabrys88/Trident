#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstdint>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>
#include <winsock2.h>
#include <ws2tcpip.h>
#include "tts-cpp/chatterbox/engine.h"
#include "tts-cpp/chatterbox/log.h"

using mono_clock = std::chrono::steady_clock;
using args_t = std::unordered_map<std::string, std::string>;

static bool recv_all(SOCKET s, void* dst, std::size_t n) {
    auto* p = static_cast<char*>(dst);
    for (std::size_t done = 0; done < n;) {
        int got = recv(s, p + done, static_cast<int>(std::min<std::size_t>(n - done, 1 << 20)), 0);
        if (got <= 0) return false;
        done += got;
    }
    return true;
}

static void send_all(SOCKET s, const void* src, std::size_t n) {
    auto* p = static_cast<const char*>(src);
    for (std::size_t done = 0; done < n;) {
        int sent = send(s, p + done, static_cast<int>(std::min<std::size_t>(n - done, 1 << 20)), 0);
        if (sent <= 0) throw std::runtime_error("TTS send failed");
        done += sent;
    }
}

static void frame(SOCKET s, std::uint32_t kind, std::uint32_t epoch, const void* data, std::size_t n) {
    std::uint32_t header[] = {kind, epoch, static_cast<std::uint32_t>(n)};
    send_all(s, header, sizeof(header));
    if (n) send_all(s, data, n);
}

static void pcm(SOCKET s, std::uint32_t epoch, const float* samples, std::size_t n) {
    std::vector<std::int16_t> out(n);
    for (std::size_t i = 0; i < n; ++i) out[i] = static_cast<std::int16_t>(std::clamp(samples[i], -1.0f, 1.0f) * 32767.0f);
    frame(s, 2, epoch, out.data(), out.size() * sizeof(std::int16_t));
}

static tts_cpp::chatterbox::Engine engine(const args_t& a) {
    tts_cpp::chatterbox::EngineOptions o;
    o.t3_gguf_path = a.at("--model");
    o.s3gen_gguf_path = a.at("--s3gen-gguf");
    o.reference_audio = a.at("--reference");
    o.language = a.at("--language");
    o.n_gpu_layers = std::stoi(a.at("--n-gpu-layers"));
    o.n_threads = std::stoi(a.at("--threads"));
    o.seed = std::stoi(a.at("--seed"));
    o.n_predict = std::stoi(a.at("--max-tokens"));
    o.n_ctx = std::stoi(a.at("--context"));
    o.top_k = std::stoi(a.at("--top-k"));
    o.top_p = std::stof(a.at("--top-p"));
    o.min_p = std::stof(a.at("--min-p"));
    o.temperature = std::stof(a.at("--temperature"));
    o.repeat_penalty = std::stof(a.at("--repeat-penalty"));
    o.cfg_weight = std::stof(a.at("--cfg-weight"));
    o.exaggeration = std::stof(a.at("--exaggeration"));
    o.cfm_steps = std::stoi(a.at("--cfm-steps"));
    o.fastconv = std::stoi(a.at("--fastconv")) != 0;
    return tts_cpp::chatterbox::Engine(o);
}

static bool request(SOCKET s, std::uint32_t& epoch, std::vector<std::string>& texts) {
    std::uint32_t header[2];
    if (!recv_all(s, header, sizeof(header))) return false;
    texts.clear();
    epoch = header[1];
    for (std::uint32_t i = 0; i < header[0]; ++i) {
        std::uint32_t n;
        if (!recv_all(s, &n, sizeof(n))) return false;
        std::string text(n, '\0');
        if (!recv_all(s, text.data(), n)) return false;
        if (!text.empty()) texts.push_back(std::move(text));
    }
    return true;
}

static void serve(SOCKET client, tts_cpp::chatterbox::Engine& tts) {
    std::mutex mu;
    std::condition_variable cv;
    std::vector<std::string> pending;
    std::atomic<std::uint32_t> live{0};
    bool stop = false;
    std::thread synth([&] {
        mono_clock::time_point last{};
        bool had_last = false;
        for (;;) {
            std::vector<std::string> batch;
            std::uint32_t epoch;
            {
                std::unique_lock lock(mu);
                cv.wait(lock, [&] { return stop || !pending.empty(); });
                if (stop && pending.empty()) return;
                batch.swap(pending);
                epoch = live.load();
            }
            auto started = mono_clock::now();
            if (had_last) tts_emit("tts.gap", ",\"epoch\":" + std::to_string(epoch) + ",\"gap_ms\":" + std::to_string(std::chrono::duration_cast<std::chrono::milliseconds>(started - last).count()));
            tts_emit("tts.synth", ",\"epoch\":" + std::to_string(epoch) + ",\"pieces\":" + std::to_string(batch.size()));
            try {
                tts.synthesize_pieces_streaming(batch, [&](int, const float* data, std::size_t n, int, bool) {
                    if (n && live.load() == epoch) pcm(client, epoch, data, n);
                });
                if (live.load() == epoch) {
                    frame(client, 0, epoch, "ok", 2);
                    tts_emit("tts.ok", ",\"epoch\":" + std::to_string(epoch) + ",\"elapsed_ms\":" + std::to_string(std::chrono::duration_cast<std::chrono::milliseconds>(mono_clock::now() - started).count()));
                }
            } catch (const std::exception& e) {
                if (live.load() != epoch) {
                    tts_emit("tts.cancel", ",\"epoch\":" + std::to_string(epoch) + ",\"live\":" + std::to_string(live.load()) + ",\"error\":" + tts_json_escape(e.what()));
                    continue;
                }
                tts_emit("tts.fail", ",\"epoch\":" + std::to_string(epoch) + ",\"error\":" + tts_json_escape(e.what()));
                std::exit(1);
            }
            last = mono_clock::now();
            had_last = true;
        }
    });
    std::uint32_t epoch;
    std::vector<std::string> texts;
    while (request(client, epoch, texts)) {
        {
            std::lock_guard lock(mu);
            if (epoch != live.load()) {
                tts_emit("tts.cancel", ",\"live\":" + std::to_string(live.load()) + ",\"epoch\":" + std::to_string(epoch) + ",\"queued\":" + std::to_string(pending.size()));
                live.store(epoch);
                tts.cancel();
                pending.clear();
            }
            pending.insert(pending.end(), texts.begin(), texts.end());
        }
        cv.notify_one();
    }
    {
        std::lock_guard lock(mu);
        stop = true;
        pending.clear();
        live.fetch_add(1);
        tts.cancel();
    }
    cv.notify_one();
    synth.join();
}

int main(int argc, char** argv) {
    try {
        args_t a;
        for (int i = 1; i + 1 < argc; i += 2) a[argv[i]] = argv[i + 1];
        auto tts = engine(a);
        WSADATA wsa{};
        if (WSAStartup(MAKEWORD(2, 2), &wsa)) throw std::runtime_error("WSAStartup failed");
        SOCKET listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listener == INVALID_SOCKET) throw std::runtime_error("socket failed");
        int reuse = 1; setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&reuse), sizeof(reuse));
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(static_cast<unsigned short>(std::stoi(a.at("--port"))));
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(listener, reinterpret_cast<sockaddr*>(&addr), sizeof(addr))) throw std::runtime_error("bind failed");
        if (listen(listener, 1)) throw std::runtime_error("listen failed");
        tts_emit("tts.ready", ",\"port\":" + std::to_string(ntohs(addr.sin_port)) + ",\"family\":" + tts_json_escape(a.at("--family")) + ",\"language\":" + tts_json_escape(a.at("--language")));
        for (;;) {
            SOCKET client = accept(listener, nullptr, nullptr);
            if (client == INVALID_SOCKET) throw std::runtime_error("accept failed");
            tts_emit("tts.client");
            serve(client, tts);
            closesocket(client);
            tts_emit("tts.client_gone");
        }
    } catch (const std::exception& e) {
        tts_emit("tts.fail", ",\"error\":" + tts_json_escape(e.what()));
        return 1;
    }
}
