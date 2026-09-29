#include "common/audio.h"
#include "common/config.h"
#include "common/silero_vad.h"
#include "common/wasapi_capture.h"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <numeric>
#include <string>
#include <vector>

namespace {

void put16(std::ofstream& out, int value) {
    out.put((char)(value & 255));
    out.put((char)((value >> 8) & 255));
}

void put32(std::ofstream& out, int value) {
    for (int i = 0; i < 4; ++i) out.put((char)((value >> (8 * i)) & 255));
}

void write_wav(const std::filesystem::path& path, const std::vector<float>& samples, int rate) {
    std::ofstream out(path, std::ios::binary);
    const int data = (int)samples.size() * 2;
    out << "RIFF";
    put32(out, 36 + data);
    out << "WAVE" << "fmt ";
    put32(out, 16);
    put16(out, 1);
    put16(out, 1);
    put32(out, rate);
    put32(out, rate * 2);
    put16(out, 2);
    put16(out, 16);
    out << "data";
    put32(out, data);
    for (float sample : samples) {
        int value = (int)std::lround(std::max(-1.f, std::min(1.f, sample)) * 32767.f);
        out.put((char)(value & 255));
        out.put((char)((value >> 8) & 255));
    }
}

std::vector<float> pull_16k(std::vector<float>& native, int native_rate, int rate, int keep) {
    if ((int)native.size() < keep + native_rate / 10) return {};
    int take = (int)native.size() - keep;
    int down = native_rate / std::gcd(rate, native_rate);
    take -= take % down;
    if (take <= 0) return {};
    std::vector<float> chunk(native.begin(), native.begin() + take + keep);
    trident::Audio audio(std::move(chunk), native_rate);
    auto converted = audio.resample(rate).samples;
    int drop = keep * rate / native_rate;
    if (drop > (int)converted.size()) drop = (int)converted.size();
    native.erase(native.begin(), native.begin() + take);
    return std::vector<float>(converted.begin() + drop, converted.end());
}

bool is_file(const std::filesystem::path& path) {
    std::error_code ec;
    return std::filesystem::is_regular_file(path, ec);
}

void publish_line(const std::filesystem::path& path, const std::string& body) {
    const auto tmp = path.parent_path() / (path.filename().string() + ".tmp");
    trident::write_named(tmp, body);
    if (!MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        std::error_code ec;
        std::filesystem::remove(tmp, ec);
        trident::fail("cannot publish " + trident::path_u8(path));
    }
}

std::filesystem::path resident_pid_path;

void remove_resident_pid() {
    if (resident_pid_path.empty()) return;
    std::error_code ec;
    std::filesystem::remove(resident_pid_path, ec);
    resident_pid_path.clear();
}

struct Ear {
    trident::CaptureDevice device;
    trident::SileroVad vad;
    int rate;
    int window;
    int pad;
    int max_samples;
    int keep = 960;
    std::vector<float> native, pcm, speech, lead, frame;
    bool talking = false;

    Ear(trident::CaptureDevice dev, const std::filesystem::path& model, int rate_in, int window_in, float threshold,
        int min_silence_ms, int pad_in, int max_samples_in)
        : device(std::move(dev)),
          vad(model, rate_in, window_in, threshold, min_silence_ms),
          rate(rate_in),
          window(window_in),
          pad(pad_in),
          max_samples(max_samples_in),
          frame(std::max(window_in, window_in * device.native_rate / rate_in)) {}

    bool pump(bool drop) {
        device.read(frame.data(), (int)frame.size());
        if (drop) {
            native.clear();
            pcm.clear();
            speech.clear();
            lead.clear();
            if (talking) {
                vad.reset();
                talking = false;
            }
            return false;
        }
        native.insert(native.end(), frame.begin(), frame.end());
        auto converted = pull_16k(native, device.native_rate, rate, keep);
        pcm.insert(pcm.end(), converted.begin(), converted.end());
        while ((int)pcm.size() >= window) {
            auto event = vad.feed(pcm.data(), window);
            std::vector<float> hop(pcm.begin(), pcm.begin() + window);
            pcm.erase(pcm.begin(), pcm.begin() + window);
            if (event.start) {
                const int n = pad < (int)lead.size() ? pad : (int)lead.size();
                speech.assign(lead.end() - n, lead.end());
                speech.insert(speech.end(), hop.begin(), hop.end());
                lead.clear();
                talking = true;
                if (max_samples > 0 && (int)speech.size() >= max_samples) {
                    vad.reset();
                    talking = false;
                    return true;
                }
                continue;
            }
            if (!talking) {
                lead.insert(lead.end(), hop.begin(), hop.end());
                if ((int)lead.size() > pad) lead.erase(lead.begin(), lead.end() - pad);
                continue;
            }
            speech.insert(speech.end(), hop.begin(), hop.end());
            if ((max_samples > 0 && (int)speech.size() >= max_samples) || event.end) {
                vad.reset();
                talking = false;
                return true;
            }
        }
        return false;
    }
};

std::string save_utterance(std::vector<float>& speech, int rate) {
    auto txt = trident::reserve_output("vad");
    auto wav = txt;
    wav.replace_extension(".wav");
    write_wav(wav, speech, rate);
    speech.clear();
    const auto name = trident::path_u8(wav.filename());
    trident::write_named(txt, name);
    return name;
}

int run(const std::map<std::string, std::string>& values, bool resident) {
    const int rate = trident::cfg_int(values, "vad.rate");
    const int window = trident::cfg_int(values, "vad.window");
    const auto device_name = trident::need(values, "vad.device");
    const int max_ms = trident::cfg_int(values, "vad.max-ms");
    auto opened = trident::CaptureDevice::open(device_name);
    std::fprintf(stderr, "vad mic: %s\n", device_name.c_str());
    std::fflush(stderr);
    const auto dir = std::filesystem::current_path();
    if (resident) {
        resident_pid_path = dir / "vad.pid";
        std::atexit(remove_resident_pid);
        std::error_code ec;
        std::filesystem::remove(dir / "vad.utterance.txt", ec);
        if (is_file(dir / "vad.stop")) {
            std::filesystem::remove(dir / "vad.stop", ec);
            std::fprintf(stderr, "resident stop before ready\n");
            std::fflush(stderr);
            return 0;
        }
        publish_line(resident_pid_path, std::to_string(GetCurrentProcessId()) + "\nready\n");
        std::fprintf(stderr, "resident ready pid %lu\n", static_cast<unsigned long>(GetCurrentProcessId()));
        std::fflush(stderr);
    }
    Ear ear(std::move(opened), trident::cfg_path(values, "vad.model"), rate, window, trident::cfg_float(values, "vad.threshold"),
        trident::cfg_int(values, "vad.min-silence-ms"), rate * trident::cfg_int(values, "vad.speech-pad-ms") / 1000,
        rate * max_ms / 1000);
    for (;;) {
        if (resident && is_file(dir / "vad.stop")) {
            std::error_code ec;
            std::filesystem::remove(dir / "vad.stop", ec);
            std::fprintf(stderr, "resident stop pid %lu\n", static_cast<unsigned long>(GetCurrentProcessId()));
            std::fflush(stderr);
            remove_resident_pid();
            return 0;
        }
        const bool drop = resident && (is_file(dir / "vad.hold") || is_file(dir / "vad.utterance.txt"));
        if (!ear.pump(drop)) continue;
        if (ear.speech.empty()) continue;
        const auto name = save_utterance(ear.speech, rate);
        if (!resident) break;
        publish_line(dir / "vad.utterance.txt", name + "\n");
    }
    return 0;
}

}

int main(int argc, char** argv) {
    const char* file = nullptr;
    bool resident = false;
    if (argc == 2 && argv[1] && argv[1][0]) {
        file = argv[1];
    } else if (argc == 3 && argv[1] && std::string(argv[1]) == "--resident" && argv[2] && argv[2][0]) {
        resident = true;
        file = argv[2];
    } else {
        trident::fail("usage: vad.exe file.txt | vad.exe --resident file.txt");
    }
    char* args[] = {argv[0], const_cast<char*>(file)};
    return run(trident::load_settings(2, args), resident);
}
