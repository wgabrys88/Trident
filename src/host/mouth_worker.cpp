#include "common/chatterbox_runtime.h"
#include "engine.h"
#include "paths.hpp"
#include <httplib.h>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <mmsystem.h>
#include <memory>
#include <thread>
#include <vector>
#include <windows.h>

#pragma comment(lib, "winmm.lib")

namespace {
using json = nlohmann::json;
using trident::host::root;

json http_json(httplib::Client& client, const std::string& method, const std::string& path, const json& body = nullptr,
               int timeout = 70) {
    client.set_connection_timeout(timeout, 0);
    client.set_read_timeout(timeout, 0);
    httplib::Result res;
    if (method == "GET") res = client.Get(path.c_str());
    else res = client.Post(path.c_str(), body.dump(), "application/json");
    if (!res) throw std::runtime_error("http " + path);
    auto data = json::parse(res->body);
    if (data.is_object() && data.contains("error")) throw std::runtime_error(data["error"].get<std::string>());
    return data;
}

void play_pcm(const std::vector<float>& pcm) {
    std::vector<int16_t> samples(pcm.size());
    for (size_t i = 0; i < pcm.size(); ++i) samples[i] = int16_t(std::clamp(pcm[i], -1.f, 1.f) * 32767.f);
    std::vector<char> bytes(samples.size() * sizeof(int16_t));
    std::memcpy(bytes.data(), samples.data(), bytes.size());
    WAVEFORMATEX fmt{};
    fmt.wFormatTag = WAVE_FORMAT_PCM;
    fmt.nChannels = 1;
    fmt.nSamplesPerSec = 24000;
    fmt.wBitsPerSample = 16;
    fmt.nBlockAlign = fmt.nChannels * fmt.wBitsPerSample / 8;
    fmt.nAvgBytesPerSec = fmt.nSamplesPerSec * fmt.nBlockAlign;
    HWAVEOUT out{};
    if (waveOutOpen(&out, WAVE_MAPPER, &fmt, 0, 0, CALLBACK_NULL) != MMSYSERR_NOERROR)
        throw std::runtime_error("waveOutOpen failed");
    WAVEHDR hdr{};
    hdr.lpData = bytes.data();
    hdr.dwBufferLength = static_cast<DWORD>(bytes.size());
    waveOutPrepareHeader(out, &hdr, sizeof(hdr));
    waveOutWrite(out, &hdr, sizeof(hdr));
    while (!(hdr.dwFlags & WHDR_DONE)) std::this_thread::sleep_for(std::chrono::milliseconds(5));
    waveOutUnprepareHeader(out, &hdr, sizeof(hdr));
    waveOutClose(out);
}

std::filesystem::path write_wav(const std::vector<float>& pcm) {
    auto folder = root() / "wav";
    std::filesystem::create_directories(folder);
    auto path = folder / (std::to_string(std::chrono::system_clock::now().time_since_epoch().count()) + ".wav");
    trident::chatterbox_write_wav(path, pcm);
    return path;
}

const char* spoken_only(const std::string& variant) {
    if (variant == "v3") return nullptr;
    return "en";
}
} // namespace

int main(int argc, char** argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: trident-mouth <nano|turbo|v3> <server>\n");
        return 2;
    }
    std::string variant = argv[1];
    if (variant != "nano" && variant != "turbo" && variant != "v3") {
        fprintf(stderr, "unknown variant %s\n", variant.c_str());
        return 2;
    }
    std::string server = argv[2];
    httplib::Client client(server);
    int gpu = http_json(client, "GET", "/config")["vulkan_device"].get<int>();
    std::unique_ptr<trident::Synth> engine;
    try {
        engine = trident::chatterbox_make_engine(variant, gpu);
    } catch (const std::exception& err) {
        fprintf(stderr, "mouth engine load failed: %s\n", err.what());
        return 1;
    }
    try {
        for (const auto& entry : std::filesystem::directory_iterator(root() / "wav")) {
            if (entry.path().extension() == ".wav") {
                json body = {{"path", std::filesystem::relative(entry.path(), root()).generic_string()}, {"kind", "wav"}};
                http_json(client, "POST", "/archive", body);
            }
        }
        http_json(client, "POST", "/ready", json{{"name", "mouth"}});
        const char* allowed = spoken_only(variant);
        for (;;) {
            if (http_json(client, "GET", "/health")["stop"].get<bool>()) break;
            json speech;
            try {
                speech = http_json(client, "GET", "/speech/next?timeout=30", nullptr, 40)["speech"];
            } catch (const std::exception& err) {
                fprintf(stderr, "speech poll failed %s\n", err.what());
                std::this_thread::sleep_for(std::chrono::seconds(1));
                continue;
            }
            if (speech.is_null()) continue;
            std::string language = speech.value("language", "en");
            std::string text = speech.value("text", "");
            std::string spoken = language;
            if (allowed && language != allowed && !language.empty()) {
                spoken = allowed;
                json body = {{"type", "mouth_language"},
                             {"source", "mouth"},
                             {"data",
                              {{"requested", language}, {"spoken_as", spoken}, {"speech", speech["name"]}}}};
                http_json(client, "POST", "/event", body);
            }
            auto pcm = engine->synthesize(text, spoken);
            auto wav = write_wav(pcm);
            fprintf(stderr, "%s\n", text.c_str());
            play_pcm(pcm);
            json done = {{"name", speech["name"]},
                         {"wav", std::filesystem::relative(wav, root()).generic_string()}};
            http_json(client, "POST", "/speech/done", done);
        }
    } catch (const std::exception& err) {
        fprintf(stderr, "mouth error: %s\n", err.what());
        return 1;
    }
    return 0;
}
