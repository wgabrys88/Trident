#include "common/audio.h"
#include "common/config.h"
#include "common/silero_vad.h"
#include "common/wasapi_capture.h"
#include <algorithm>
#include <cmath>
#include <cstdint>
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

}

int main(int, char**) {
    const auto values = trident::load_trident();
    if (trident::cfg_on(values, "vad.unload")) return trident::unload_named("vad");
    if (trident::resident("vad")) return 0;
    const int rate = trident::cfg_int(values, "vad.rate");
    const int window = trident::cfg_int(values, "vad.window");
    auto device = trident::CaptureDevice::open(trident::need(values, "vad.device"));
    trident::SileroVad vad(trident::cfg_path(values, "vad.model"), rate, window, trident::cfg_float(values, "vad.threshold"),
        trident::cfg_int(values, "vad.min-silence-ms"));
    const int pad = rate * trident::cfg_int(values, "vad.speech-pad-ms") / 1000;
    const auto prompt = trident::cfg_path(values, "ear.prompt-file");
    const auto chunks = trident::trident_file().parent_path() / std::filesystem::u8path(trident::need(values, "vad.chunks-dir"));
    std::filesystem::create_directories(chunks);
    trident::write_pid("vad");
    std::filesystem::remove(trident::slot("vad", "stop"));
    std::vector<float> native, pcm, speech, lead;
    bool talking = false;
    int index = 0;
    const int block = std::max(window, window * device.native_rate / rate);
    std::vector<float> frame(block);
    const int keep = 960;
    while (!std::filesystem::exists(trident::slot("vad", "stop"))) {
        device.read(frame.data(), block);
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
                continue;
            }
            if (!talking) {
                lead.insert(lead.end(), hop.begin(), hop.end());
                if ((int)lead.size() > pad) lead.erase(lead.begin(), lead.end() - pad);
                continue;
            }
            speech.insert(speech.end(), hop.begin(), hop.end());
            if (!event.end) continue;
            vad.reset();
            talking = false;
            ++index;
            auto wav = chunks / ("utt-" + std::to_string(index) + ".wav");
            write_wav(wav, speech, rate);
            speech.clear();
            auto relative = std::filesystem::relative(wav, trident::trident_file().parent_path());
            std::string text = relative.generic_string();
            std::ofstream(prompt, std::ios::binary | std::ios::trunc) << text;
        }
    }
    device.close();
    std::filesystem::remove(trident::slot("vad", "pid"));
    std::filesystem::remove(trident::slot("vad", "stop"));
    return 0;
}
