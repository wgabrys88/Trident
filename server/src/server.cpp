#include "server.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <future>
#include <iostream>
#include <mutex>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using json = nlohmann::json;

namespace {

std::mutex trace_output;
const auto native_epoch = std::chrono::steady_clock::now();

double native_ms() {
    return std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - native_epoch).count();
}

json identifiers(const json& message) {
    json result = json::object();
    for (const char* key : {
             "trace_id", "turn_id", "config_id", "session_id",
             "request_id", "lane", "client_id"}) {
        if (message.contains(key) && message.at(key).is_string()) {
            const auto value = message.at(key).get<std::string>();
            if (!value.empty()) result[key] = value;
        }
    }
    return result;
}

void merge(json& destination, const json& source) {
    for (const auto& item : source.items()) destination[item.key()] = item.value();
}

json correlated(const std::string& type, const json& context, json data = json::object()) {
    data["type"] = type;
    merge(data, context);
    return data;
}

void native_event(
    const std::string& level,
    const std::string& event,
    const json& context = json::object(),
    const json& data = json::object(),
    const std::string& message = "") {
    json payload{
        {"schema", "trident.native-event"},
        {"version", 1},
        {"source", "tts-native"},
        {"component", "tts"},
        {"level", level},
        {"event", event},
        {"native_ms", native_ms()},
        {"message", message},
        {"data", data},
    };
    merge(payload, context);
    std::lock_guard<std::mutex> lock(trace_output);
    std::cout << "TRIDENT_EVENT " << payload.dump() << std::endl;
}

void write_pcm16_wav(
    const std::string& path,
    const std::vector<int16_t>& samples,
    uint32_t sample_rate) {
    const std::filesystem::path target(path);
    if (!target.parent_path().empty()) {
        std::filesystem::create_directories(target.parent_path());
    }
    std::ofstream output(target, std::ios::binary | std::ios::trunc);
    if (!output) throw std::runtime_error("cannot open diagnostic WAV: " + path);
    const uint32_t data_size = static_cast<uint32_t>(samples.size() * sizeof(int16_t));
    const uint32_t riff_size = 36 + data_size;
    const uint32_t byte_rate = sample_rate * sizeof(int16_t);
    const uint16_t format = 1;
    const uint16_t channels = 1;
    const uint16_t block_align = sizeof(int16_t);
    const uint16_t bits = 16;
    output.write("RIFF", 4);
    output.write(reinterpret_cast<const char*>(&riff_size), 4);
    output.write("WAVEfmt ", 8);
    const uint32_t fmt_size = 16;
    output.write(reinterpret_cast<const char*>(&fmt_size), 4);
    output.write(reinterpret_cast<const char*>(&format), 2);
    output.write(reinterpret_cast<const char*>(&channels), 2);
    output.write(reinterpret_cast<const char*>(&sample_rate), 4);
    output.write(reinterpret_cast<const char*>(&byte_rate), 4);
    output.write(reinterpret_cast<const char*>(&block_align), 2);
    output.write(reinterpret_cast<const char*>(&bits), 2);
    output.write("data", 4);
    output.write(reinterpret_cast<const char*>(&data_size), 4);
    output.write(reinterpret_cast<const char*>(samples.data()), data_size);
    if (!output) throw std::runtime_error("cannot write diagnostic WAV: " + path);
}

} // namespace

namespace tts {

TTSServer::TTSServer(int port) : port_(port) {}

TTSServer::~TTSServer() {
    stop();
}

void TTSServer::initialize(
    const std::string& t3,
    const std::string& s3,
    int gpu,
    int threads,
    int context,
    int sessions) {
    native_event("info", "tts.native.initializing", {}, {
        {"t3_model", t3},
        {"s3gen_model", s3},
        {"gpu_layers", gpu},
        {"threads", threads},
        {"context", context},
        {"max_sessions", sessions},
    });
    engine_ = std::make_unique<EngineWrapper>();
    engine_->initialize(t3, s3, gpu, threads, context, sessions);
    native_event("info", "tts.native.initialized", {}, {
        {"gpu_layers", gpu},
        {"threads", threads},
        {"context", context},
        {"max_sessions", sessions},
    });

    http_.Get("/health", [](const httplib::Request&, httplib::Response& response) {
        response.set_content("{\"status\":\"ok\"}", "application/json");
    });
    http_.Get("/state", [this](const httplib::Request&, httplib::Response& response) {
        response.set_content(json{{"sessions", engine_->session_count()}}.dump(), "application/json");
    });
    http_.Post("/cancel", [this](const httplib::Request& request, httplib::Response& response) {
        try {
            const auto body = json::parse(request.body);
            const auto id = body.at("session_id").get<std::string>();
            const auto context = identifiers(body);
            if (!engine_->cancel(id)) {
                native_event("warn", "tts.cancel.not_found", context, {{"session_id", id}});
                response.status = 404;
                response.set_content(json{{"error", "session not found"}}.dump(), "application/json");
                return;
            }
            native_event("info", "tts.cancel.accepted", context, {{"session_id", id}});
            response.set_content(json{{"cancelled", true}, {"session_id", id}}.dump(), "application/json");
        } catch (const std::exception& exception) {
            native_event("error", "tts.cancel.failed", {}, {}, exception.what());
            response.status = 400;
            response.set_content(json{{"error", exception.what()}}.dump(), "application/json");
        }
    });
    http_.WebSocket("/tts", [this](const httplib::Request&, httplib::ws::WebSocket& socket) {
        connect(socket);
    });
}

void TTSServer::start() {
    running_ = true;
    native_event("info", "tts.native.listen_starting", {}, {{"host", "127.0.0.1"}, {"port", port_}});
    thread_ = std::thread([this] {
        if (!http_.listen("127.0.0.1", port_)) {
            native_event("error", "tts.native.listen_failed", {}, {{"host", "127.0.0.1"}, {"port", port_}});
        }
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
}

void TTSServer::stop() {
    if (!running_.exchange(false)) return;
    native_event("info", "tts.native.stopping", {}, {{"port", port_}});
    http_.stop();
    if (thread_.joinable()) thread_.join();
    native_event("info", "tts.native.stopped", {}, {{"port", port_}});
}

void TTSServer::connect(httplib::ws::WebSocket& socket) {
    std::string session;
    std::string capture_audio;
    json session_context = json::object();
    std::future<void> work;
    bool open = true;
    native_event("info", "tts.socket.connected");

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

                VoiceConfig config;
                config.reference = message.at("reference_audio").get<std::string>();
                config.language = message.at("language").get<std::string>();
                config.seed = message.at("seed").get<int>();
                config.max_tokens = message.at("max_tokens").get<int>();
                config.top_k = message.at("top_k").get<int>();
                config.cfm_steps = message.at("cfm_steps").get<int>();
                config.first_chunk = message.at("stream_first_chunk_tokens").get<int>();
                config.chunk = message.at("stream_chunk_tokens").get<int>();
                config.max_sentence_chars = message.value("max_sentence_chars", 180);
                config.exaggeration = message.at("exaggeration").get<float>();
                config.cfg = message.at("cfg_weight").get<float>();
                config.temperature = message.at("temperature").get<float>();
                config.repeat = message.at("repeat_penalty").get<float>();
                config.min_p = message.at("min_p").get<float>();
                config.top_p = message.at("top_p").get<float>();

                session_context = identifiers(message);
                capture_audio = (std::filesystem::path(config.reference).parent_path() / "last-output.wav").string();
                if (!std::filesystem::is_regular_file(config.reference)) {
                    throw std::runtime_error("reference audio not found: " + config.reference);
                }
                native_event("info", "tts.session.initializing", session_context, {
                    {"reference_audio", config.reference},
                    {"language", config.language},
                    {"seed", config.seed},
                    {"max_tokens", config.max_tokens},
                    {"top_k", config.top_k},
                    {"top_p", config.top_p},
                    {"min_p", config.min_p},
                    {"temperature", config.temperature},
                    {"repeat_penalty", config.repeat},
                    {"cfg_weight", config.cfg},
                    {"exaggeration", config.exaggeration},
                    {"cfm_steps", config.cfm_steps},
                    {"first_chunk_tokens", config.first_chunk},
                    {"chunk_tokens", config.chunk},
                    {"max_sentence_chars", config.max_sentence_chars},
                    {"capture_audio", capture_audio},
                });
                session = engine_->create_session(config);
                session_context["session_id"] = session;
                native_event("info", "tts.session.ready", session_context, {
                    {"language", config.language},
                    {"sample_rate", 24000},
                    {"format", "pcm_s16le"},
                });
                socket.send(correlated("ready", session_context, {
                    {"language", config.language},
                    {"sample_rate", 24000},
                    {"format", "pcm_s16le"},
                }).dump());
            } else if (type == "synthesize") {
                if (session.empty()) throw std::runtime_error("session not initialized");
                finish();
                const auto text = message.at("text").get<std::string>();
                auto request_context = session_context;
                merge(request_context, identifiers(message));
                if (!request_context.contains("request_id")) {
                    throw std::runtime_error("request_id is required");
                }
                socket.send(correlated("synthesize_started", request_context).dump());

                work = std::async(std::launch::async, [this, &socket, session, text, request_context, capture_audio] {
                    const auto started = std::chrono::steady_clock::now();
                    std::size_t total = 0;
                    std::size_t clipped = 0;
                    std::size_t chunks = 0;
                    double sumsq = 0;
                    float peak = 0;
                    bool final_chunk = false;
                    std::vector<int16_t> captured;
                    native_event("info", "tts.synthesis.started", request_context, {
                        {"text", text},
                        {"characters", text.size()},
                        {"capture_audio", capture_audio},
                    });
                    try {
                        engine_->synthesize(session, text, [&](const float* pcm, std::size_t samples, int index, bool last) {
                            std::vector<char> bytes(samples * 2);
                            for (std::size_t i = 0; i < samples; ++i) {
                                const float sample = std::max(-1.0f, std::min(1.0f, pcm[i]));
                                peak = std::max(peak, std::abs(pcm[i]));
                                sumsq += static_cast<double>(pcm[i]) * pcm[i];
                                clipped += std::abs(pcm[i]) >= 0.999f;
                                const auto value = static_cast<int16_t>(sample * 32767);
                                captured.push_back(value);
                                bytes[i * 2] = static_cast<char>(value & 255);
                                bytes[i * 2 + 1] = static_cast<char>((value >> 8) & 255);
                            }
                            total += samples;
                            ++chunks;
                            final_chunk = final_chunk || last;
                            socket.send(correlated("audio", request_context, {
                                {"chunk_index", index},
                                {"samples", samples},
                                {"total_samples", total},
                                {"sample_rate", 24000},
                                {"last", last},
                            }).dump());
                            socket.send(bytes.data(), bytes.size());
                            native_event("info", "tts.audio.chunk", request_context, {
                                {"chunk_index", index},
                                {"chunk_samples", samples},
                                {"total_samples", total},
                                {"chunks", chunks},
                                {"last", last},
                            });
                            if (last) {
                                const double rms = total ? std::sqrt(sumsq / total) : 0;
                                const double rms_dbfs = 20.0 * std::log10(std::max(rms, 1e-9));
                                const double peak_dbfs = 20.0 * std::log10(std::max(static_cast<double>(peak), 1e-9));
                                const double clip_pct = total ? 100.0 * clipped / total : 0.0;
                                if (!capture_audio.empty()) {
                                    try {
                                        write_pcm16_wav(capture_audio, captured, 24000);
                                    } catch (const std::exception& exception) {
                                        native_event("error", "tts.audio.capture_failed", request_context, {
                                            {"capture_audio", capture_audio},
                                        }, exception.what());
                                    }
                                }
                                const json metrics{
                                    {"samples", total},
                                    {"chunks", chunks},
                                    {"seconds", total / 24000.0},
                                    {"sample_rate", 24000},
                                    {"rms_dbfs", rms_dbfs},
                                    {"peak_dbfs", peak_dbfs},
                                    {"clip_pct", clip_pct},
                                    {"capture_audio", capture_audio},
                                };
                                native_event("info", "tts.audio.completed", request_context, metrics);
                                socket.send(correlated("chunk_done", request_context, metrics).dump());
                            }
                        });
                        const double duration_ms = std::chrono::duration<double, std::milli>(
                            std::chrono::steady_clock::now() - started).count();
                        native_event("info", "tts.synthesis.completed", request_context, {
                            {"samples", total},
                            {"chunks", chunks},
                            {"seconds", total / 24000.0},
                            {"duration_ms", duration_ms},
                            {"final_chunk", final_chunk},
                        });
                    } catch (const std::exception& exception) {
                        const std::string detail = exception.what();
                        const bool cancelled = detail.find("cancelled") != std::string::npos;
                        const std::string event = cancelled ? "cancelled" : "error";
                        native_event(cancelled ? "warn" : "error", cancelled ? "tts.synthesis.cancelled" : "tts.synthesis.failed", request_context, {
                            {"samples", total},
                            {"chunks", chunks},
                        }, detail);
                        socket.send(correlated(event, request_context, {
                            {"message", detail},
                            {"samples", total},
                            {"chunks", chunks},
                        }).dump());
                    }
                });
            } else if (type == "cancel") {
                if (session.empty() || !engine_->cancel(session)) {
                    throw std::runtime_error("session not initialized");
                }
                native_event("info", "tts.cancel.accepted", session_context);
            } else if (type == "close") {
                if (!session.empty()) engine_->cancel(session);
                native_event("info", "tts.socket.close_requested", session_context);
                open = false;
            } else {
                throw std::runtime_error("unknown message: " + type);
            }
        } catch (const std::exception& exception) {
            native_event("error", "tts.socket.message_failed", session_context, {}, exception.what());
            socket.send(correlated("error", session_context, {{"message", exception.what()}}).dump());
        }
    }

    if (!session.empty()) engine_->cancel(session);
    finish();
    if (!session.empty()) {
        engine_->destroy_session(session);
        native_event("info", "tts.session.destroyed", session_context);
    }
    native_event("info", "tts.socket.disconnected", session_context);
}

} // namespace tts
