#include "server.hpp"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using json = nlohmann::json;

namespace {

void write_wav(const std::string& path, const std::vector<float>& pcm, uint32_t rate) {
    std::vector<int16_t> samples(pcm.size());
    for (size_t i = 0; i < pcm.size(); ++i) {
        const float s = std::max(-1.0f, std::min(1.0f, pcm[i]));
        samples[i] = static_cast<int16_t>(s * 32767);
    }
    const auto target = std::filesystem::path(path);
    if (!target.parent_path().empty()) std::filesystem::create_directories(target.parent_path());
    std::ofstream out(target, std::ios::binary | std::ios::trunc);
    if (!out) throw std::runtime_error("cannot open WAV: " + path);
    const uint32_t data_size = static_cast<uint32_t>(samples.size() * 2);
    const uint32_t riff_size = 36 + data_size;
    const uint32_t byte_rate = rate * 2;
    const uint32_t fmt_size = 16;
    const uint16_t format = 1, channels = 1, block_align = 2, bits = 16;
    out.write("RIFF", 4);
    out.write(reinterpret_cast<const char*>(&riff_size), 4);
    out.write("WAVEfmt ", 8);
    out.write(reinterpret_cast<const char*>(&fmt_size), 4);
    out.write(reinterpret_cast<const char*>(&format), 2);
    out.write(reinterpret_cast<const char*>(&channels), 2);
    out.write(reinterpret_cast<const char*>(&rate), 4);
    out.write(reinterpret_cast<const char*>(&byte_rate), 4);
    out.write(reinterpret_cast<const char*>(&block_align), 2);
    out.write(reinterpret_cast<const char*>(&bits), 2);
    out.write("data", 4);
    out.write(reinterpret_cast<const char*>(&data_size), 4);
    out.write(reinterpret_cast<const char*>(samples.data()), data_size);
}

tts::Voice voice_from(const json& body) {
    tts::Voice voice;
    voice.reference = body.at("reference").get<std::string>();
    voice.language = body.at("language").get<std::string>();
    voice.reference_mtime = body.value("reference_mtime", 0.0);
    voice.seed = body.at("seed").get<int>();
    voice.max_tokens = body.at("max_tokens").get<int>();
    voice.top_k = body.at("top_k").get<int>();
    voice.cfm_steps = body.at("cfm_steps").get<int>();
    voice.chunk_chars = body.value("chunk_chars", 300);
    voice.exaggeration = body.at("exaggeration").get<float>();
    voice.cfg = body.at("cfg_weight").get<float>();
    voice.temperature = body.at("temperature").get<float>();
    voice.repeat = body.at("repeat_penalty").get<float>();
    voice.min_p = body.at("min_p").get<float>();
    voice.top_p = body.at("top_p").get<float>();
    return voice;
}

} // namespace

namespace tts {

TTSServer::TTSServer(int port) : port_(port) {}
TTSServer::~TTSServer() { stop(); }

void TTSServer::initialize(
    const std::string& t3,
    const std::string& s3,
    int gpu,
    int threads,
    int context) {
    std::cerr << "tts init gpu=" << gpu << " threads=" << threads << " ctx=" << context << std::endl;
    engine_ = std::make_unique<EngineWrapper>();
    engine_->initialize(t3, s3, gpu, threads, context);
    http_.Get("/health", [](const httplib::Request&, httplib::Response& response) {
        response.set_content("{\"status\":\"ok\"}", "application/json");
    });
    http_.Post("/cancel", [this](const httplib::Request&, httplib::Response& response) {
        engine_->cancel();
        response.set_content("{\"cancelled\":true}", "application/json");
    });
    http_.Post("/tts", [this](const httplib::Request& request, httplib::Response& response) {
        json body;
        try {
            body = json::parse(request.body);
            if (body.at("text").get<std::string>().empty()) throw std::runtime_error("text is empty");
        } catch (const std::exception& exception) {
            response.status = 400;
            response.set_content(json{{"error", exception.what()}}.dump(), "application/json");
            return;
        }
        const auto text = body.at("text").get<std::string>();
        tts::Voice voice;
        try {
            voice = voice_from(body);
            engine_->prepare(voice, text);
        } catch (const std::exception& exception) {
            response.status = 500;
            response.set_content(json{{"error", exception.what()}}.dump(), "application/json");
            return;
        }
        const auto dir = std::filesystem::path(voice.reference).parent_path();
        const auto wav = (dir / "last-output.wav").string();
        const auto part = (dir / "last-chunk.wav").string();
        response.set_header("Cache-Control", "no-store");
        auto started = std::make_shared<std::chrono::steady_clock::time_point>(std::chrono::steady_clock::now());
        auto closed = std::make_shared<bool>(false);
        response.set_chunked_content_provider("application/x-ndjson",
            [this, voice, wav, part, dir, started, closed](size_t, httplib::DataSink& sink) {
                if (*closed) return false;
                auto emit = [&](const json& line) {
                    const auto blob = line.dump() + "\n";
                    sink.write(blob.data(), blob.size());
                };
                try {
                    if (engine_->busy()) {
                        engine_->step([&](int index, int total, const std::vector<float>& all, const std::vector<float>& playable) {
                            write_wav(wav, all, 24000);
                            write_wav(part, playable, 24000);
                            write_wav((dir / ("pack-" + std::to_string(index) + ".wav")).string(), playable, 24000);
                            emit({
                                {"ok", true}, {"done", false},
                                {"chunk", index}, {"chunks", total},
                                {"samples", all.size()},
                                {"seconds", all.size() / 24000.0},
                                {"playable", playable.size()},
                            });
                            std::cerr << "tts pack chunk=" << index + 1 << "/" << total
                                      << " playable=" << playable.size()
                                      << " samples=" << all.size() << std::endl;
                        });
                    }
                    if (engine_->busy()) return true;
                    const auto speech = engine_->finish();
                    const double ms = std::chrono::duration<double, std::milli>(
                        std::chrono::steady_clock::now() - *started).count();
                    write_wav(wav, speech.pcm, 24000);
                    emit({
                        {"ok", true}, {"done", true},
                        {"samples", speech.pcm.size()},
                        {"seconds", speech.pcm.size() / 24000.0},
                        {"ms", ms},
                        {"t3_ms", speech.t3_ms},
                        {"s3gen_ms", speech.s3gen_ms},
                        {"cfm_steps", voice.cfm_steps},
                        {"chunks", speech.chunks},
                        {"language", voice.language},
                        {"path", wav},
                    });
                    std::cerr << "tts done samples=" << speech.pcm.size()
                              << " t3_ms=" << speech.t3_ms
                              << " s3gen_ms=" << speech.s3gen_ms
                              << " ms=" << ms
                              << " cfm_steps=" << voice.cfm_steps
                              << " chunks=" << speech.chunks
                              << " lang=" << voice.language << std::endl;
                } catch (const std::exception& exception) {
                    emit({{"error", exception.what()}});
                    engine_->finish();
                }
                *closed = true;
                sink.done();
                return false;
            });
    });
}

void TTSServer::start() {
    running_ = true;
    thread_ = std::thread([this] {
        if (!http_.listen("127.0.0.1", port_)) std::cerr << "tts listen failed port=" << port_ << std::endl;
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
}

void TTSServer::stop() {
    if (!running_.exchange(false)) return;
    http_.stop();
    if (thread_.joinable()) thread_.join();
}

}
