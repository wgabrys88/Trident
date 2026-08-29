#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstdint>
#include <deque>
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

struct request_t {
    std::uint32_t epoch;
    std::uint32_t response;
    std::uint32_t piece;
    std::string text;
};

static bool recv_all(SOCKET socket, void* dst, std::size_t size) {
    auto* data = static_cast<char*>(dst);
    for (std::size_t done = 0; done < size;) {
        const int got = recv(socket, data + done, static_cast<int>(std::min<std::size_t>(size - done, 1 << 20)), 0);
        if (got <= 0) return false;
        done += got;
    }
    return true;
}

static void send_all(SOCKET socket, const void* src, std::size_t size) {
    auto* data = static_cast<const char*>(src);
    for (std::size_t done = 0; done < size;) {
        const int sent = send(socket, data + done, static_cast<int>(std::min<std::size_t>(size - done, 1 << 20)), 0);
        if (sent <= 0) throw std::runtime_error("TTS send failed");
        done += sent;
    }
}

static void frame(SOCKET socket, std::uint32_t kind, const request_t& request, std::uint32_t chunk, const void* data, std::size_t size) {
    const std::uint32_t header[] = {kind, request.epoch, request.response, request.piece, chunk, static_cast<std::uint32_t>(size)};
    send_all(socket, header, sizeof(header));
    if (size) send_all(socket, data, size);
}

static void pcm(SOCKET socket, const request_t& request, std::uint32_t chunk, const float* samples, std::size_t size) {
    std::vector<std::int16_t> out(size);
    for (std::size_t i = 0; i < size; ++i) out[i] = static_cast<std::int16_t>(std::clamp(samples[i], -1.0f, 1.0f) * 32767.0f);
    frame(socket, 2, request, chunk, out.data(), out.size() * sizeof(std::int16_t));
}

static tts_cpp::chatterbox::Engine make_engine(const args_t& args) {
    tts_cpp::chatterbox::EngineOptions options;
    options.t3_gguf_path = args.at("--model");
    options.s3gen_gguf_path = args.at("--s3gen-gguf");
    options.reference_audio = args.at("--reference");
    options.language = args.at("--language");
    options.n_gpu_layers = std::stoi(args.at("--n-gpu-layers"));
    options.n_threads = std::stoi(args.at("--threads"));
    options.seed = std::stoi(args.at("--seed"));
    options.n_predict = std::stoi(args.at("--max-tokens"));
    options.n_ctx = std::stoi(args.at("--context"));
    options.top_k = std::stoi(args.at("--top-k"));
    options.top_p = std::stof(args.at("--top-p"));
    options.min_p = std::stof(args.at("--min-p"));
    options.temperature = std::stof(args.at("--temperature"));
    options.repeat_penalty = std::stof(args.at("--repeat-penalty"));
    options.cfg_weight = std::stof(args.at("--cfg-weight"));
    options.exaggeration = std::stof(args.at("--exaggeration"));
    options.cfm_steps = std::stoi(args.at("--cfm-steps"));
    options.fastconv = std::stoi(args.at("--fastconv")) != 0;
    return tts_cpp::chatterbox::Engine(options);
}

static bool receive(SOCKET socket, request_t& request) {
    std::uint32_t header[4];
    if (!recv_all(socket, header, sizeof(header))) return false;
    request.epoch = header[0];
    request.response = header[1];
    request.piece = header[2];
    request.text.resize(header[3]);
    return recv_all(socket, request.text.data(), request.text.size());
}

static std::string ids(const request_t& request) {
    return ",\"epoch\":" + std::to_string(request.epoch)
        + ",\"response_id\":" + std::to_string(request.response)
        + ",\"piece_id\":" + std::to_string(request.piece);
}

static void serve(SOCKET client, tts_cpp::chatterbox::Engine& tts) {
    std::mutex mutex;
    std::condition_variable changed;
    std::deque<request_t> pending;
    std::atomic<std::uint32_t> live{0};
    bool stop = false;
    std::thread synth([&] {
        for (;;) {
            request_t request;
            {
                std::unique_lock lock(mutex);
                changed.wait(lock, [&] { return stop || !pending.empty(); });
                if (stop) return;
                request = std::move(pending.front());
                pending.pop_front();
            }
            const auto started = mono_clock::now();
            tts_emit("tts.piece.begin", ids(request) + ",\"chars\":" + std::to_string(request.text.size()));
            try {
                std::uint32_t chunks = 0;
                tts.synthesize_pieces({request.text}, [&](int, const float* data, std::size_t size, int chunk, bool last) {
                    if (live.load() != request.epoch) return;
                    chunks = std::max(chunks, static_cast<std::uint32_t>(chunk + 1));
                    tts_emit("tts.frame", ids(request) + ",\"chunk_id\":" + std::to_string(chunk) + ",\"samples\":" + std::to_string(size) + ",\"last\":" + (last ? "true" : "false"));
                    if (size) pcm(client, request, chunk, data, size);
                    if (last) frame(client, 0, request, chunk, nullptr, 0);
                });
                if (live.load() == request.epoch) {
                    tts_emit("tts.piece.done", ids(request) + ",\"chunks\":" + std::to_string(chunks) + ",\"elapsed_ms\":" + std::to_string(std::chrono::duration_cast<std::chrono::milliseconds>(mono_clock::now() - started).count()));
                }
            } catch (const std::exception& error) {
                if (live.load() != request.epoch) {
                    tts_emit("tts.piece.cancel", ids(request));
                    continue;
                }
                tts_emit("tts.fail", ids(request) + ",\"error\":" + tts_json_escape(error.what()));
                std::exit(1);
            }
        }
    });
    request_t request;
    while (receive(client, request)) {
        {
            std::lock_guard lock(mutex);
            if (request.epoch != live.load()) {
                tts_emit("tts.epoch", ",\"from\":" + std::to_string(live.load()) + ",\"to\":" + std::to_string(request.epoch) + ",\"dropped_pieces\":" + std::to_string(pending.size()));
                live.store(request.epoch);
                tts.cancel();
                pending.clear();
            }
            if (!request.text.empty()) pending.push_back(std::move(request));
        }
        changed.notify_one();
    }
    {
        std::lock_guard lock(mutex);
        stop = true;
        pending.clear();
        live.fetch_add(1);
        tts.cancel();
    }
    changed.notify_one();
    synth.join();
}

int main(int argc, char** argv) {
    try {
        args_t args;
        for (int i = 1; i + 1 < argc; i += 2) args[argv[i]] = argv[i + 1];
        auto tts = make_engine(args);
        WSADATA wsa{};
        if (WSAStartup(MAKEWORD(2, 2), &wsa)) throw std::runtime_error("WSAStartup failed");
        const SOCKET listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listener == INVALID_SOCKET) throw std::runtime_error("socket failed");
        int reuse = 1;
        setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&reuse), sizeof(reuse));
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_port = htons(static_cast<unsigned short>(std::stoi(args.at("--port"))));
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address))) throw std::runtime_error("bind failed");
        if (listen(listener, 1)) throw std::runtime_error("listen failed");
        tts_emit("tts.ready", ",\"port\":" + std::to_string(ntohs(address.sin_port)) + ",\"family\":" + tts_json_escape(args.at("--family")) + ",\"language\":" + tts_json_escape(args.at("--language")));
        for (;;) {
            const SOCKET client = accept(listener, nullptr, nullptr);
            if (client == INVALID_SOCKET) throw std::runtime_error("accept failed");
            tts_emit("tts.client");
            serve(client, tts);
            closesocket(client);
            tts_emit("tts.client_gone");
        }
    } catch (const std::exception& error) {
        tts_emit("tts.fail", ",\"error\":" + tts_json_escape(error.what()));
        return 1;
    }
}
