#include "server.hpp"
#include <nlohmann/json.hpp>
#include <chrono>
#include <filesystem>
#include <future>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using json = nlohmann::json;

namespace tts {
TTSServer::TTSServer(int port) : port_(port) {}
TTSServer::~TTSServer() { stop(); }

void TTSServer::initialize(const std::string& t3, const std::string& s3, int gpu, int threads, int context, int sessions) {
    engine_ = std::make_unique<EngineWrapper>();
    engine_->initialize(t3, s3, gpu, threads, context, sessions);
    http_.Get("/health", [](const httplib::Request&, httplib::Response& response) { response.set_content("{\"status\":\"ok\"}", "application/json"); });
    http_.Get("/state", [this](const httplib::Request&, httplib::Response& response) { response.set_content(json{{"sessions", engine_->session_count()}}.dump(), "application/json"); });
    http_.Post("/cancel", [this](const httplib::Request& request, httplib::Response& response) {
        try {
            const auto id = json::parse(request.body).at("session_id").get<std::string>();
            if (!engine_->cancel(id)) {
                response.status = 404;
                response.set_content(json{{"error", "session not found"}}.dump(), "application/json");
                return;
            }
            response.set_content(json{{"cancelled", true}, {"session_id", id}}.dump(), "application/json");
        } catch (const std::exception& exception) {
            response.status = 400;
            response.set_content(json{{"error", exception.what()}}.dump(), "application/json");
        }
    });
    http_.WebSocket("/tts", [this](const httplib::Request&, httplib::ws::WebSocket& socket) { connect(socket); });
}

void TTSServer::start() {
    running_ = true;
    thread_ = std::thread([this] {
        if (!http_.listen("127.0.0.1", port_)) {
            std::cerr << "TTS Server: listen() failed on port " << port_ << std::endl;
        }
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
}

void TTSServer::stop() {
    if (!running_.exchange(false)) return;
    http_.stop();
    if (thread_.joinable()) thread_.join();
}

void TTSServer::connect(httplib::ws::WebSocket& socket) {
    std::string session;
    std::future<void> work;
    bool open = true;
    auto finish = [&] {
        if (work.valid()) work.get();
    };
    while (open) {
        std::string raw;
        if (socket.read(raw) != httplib::ws::Text) break;
        try {
            const auto message = json::parse(raw);
            const auto type = message.at("type").get<std::string>();
            if (type == "init") {
                if (!session.empty()) engine_->cancel(session);
                finish();
                if (!session.empty()) engine_->destroy_session(session);
                VoiceConfig config{message.at("reference_audio").get<std::string>(), message.at("language").get<std::string>(), message.at("seed").get<int>(), message.at("max_tokens").get<int>(), message.at("top_k").get<int>(), message.at("cfm_steps").get<int>(), message.at("stream_first_chunk_tokens").get<int>(), message.at("stream_chunk_tokens").get<int>(), message.value("max_sentence_chars", 180), message.at("exaggeration").get<float>(), message.at("cfg_weight").get<float>(), message.at("temperature").get<float>(), message.at("repeat_penalty").get<float>(), message.at("min_p").get<float>(), message.at("top_p").get<float>()};
                if (!std::filesystem::is_regular_file(config.reference)) throw std::runtime_error("reference audio not found: " + config.reference);
                session = engine_->create_session(config);
                socket.send(json{{"type", "ready"}, {"session_id", session}, {"language", config.language}, {"sample_rate", 24000}, {"format", "pcm_s16le"}}.dump());
            } else if (type == "synthesize") {
                if (session.empty()) throw std::runtime_error("session not initialized");
                finish();
                const auto text = message.at("text").get<std::string>();
                const auto request = message.at("request_id").get<std::string>();
                socket.send(json{{"type", "synthesize_started"}, {"request_id", request}}.dump());
                work = std::async(std::launch::async, [this, &socket, session, text, request] {
                    std::size_t total = 0;
                    try {
                        engine_->synthesize(session, text, [&](const float* pcm, std::size_t samples, int index, bool last) {
                            std::vector<char> bytes(samples * 2);
                            for (std::size_t i = 0; i < samples; ++i) {
                                const float sample = pcm[i] > 1 ? 1 : pcm[i] < -1 ? -1 : pcm[i];
                                const auto value = static_cast<int16_t>(sample * 32767);
                                bytes[i * 2] = static_cast<char>(value & 255);
                                bytes[i * 2 + 1] = static_cast<char>((value >> 8) & 255);
                            }
                            total += samples;
                            socket.send(json{{"type", "audio"}, {"request_id", request}, {"chunk_index", index}, {"samples", samples}, {"sample_rate", 24000}}.dump());
                            socket.send(bytes.data(), bytes.size());
                            if (last) socket.send(json{{"type", "chunk_done"}, {"request_id", request}, {"chunk_index", index}, {"samples", total}}.dump());
                        });
                    } catch (const std::exception& exception) {
                        const std::string detail = exception.what();
                        const std::string event = detail.find("cancelled") == std::string::npos ? "error" : "cancelled";
                        socket.send(json{{"type", event}, {"request_id", request}, {"message", detail}, {"samples", total}}.dump());
                    }
                });
            } else if (type == "cancel") {
                if (session.empty() || !engine_->cancel(session)) throw std::runtime_error("session not initialized");
            } else if (type == "close") {
                if (!session.empty()) engine_->cancel(session);
                open = false;
            } else {
                throw std::runtime_error("unknown message: " + type);
            }
        } catch (const std::exception& exception) {
            socket.send(json{{"type", "error"}, {"message", exception.what()}}.dump());
        }
    }
    if (!session.empty()) engine_->cancel(session);
    finish();
    if (!session.empty()) engine_->destroy_session(session);
}
}