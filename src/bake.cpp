#include "common/audio.h"
#include "common/voice_encoder.h"
#include "common/campplus.h"
#include "common/s3_tokenizer.h"
#include "common/gguf_file.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void usage() {
    std::fprintf(stderr,
                 "usage: chatterbox-bake <t3.gguf> <s3.gguf> <reference.wav> [knobs]\n"
                 "defaults are the current bake:\n"
                 "  --gpu 0 --cond-seconds 15|6 (gpt2|llama) --normalize-lufs -27 --trim-db 20\n"
                 "  --voice-seconds 30 --prompt-seconds 10 --mel-seconds 10\n"
                 "  --voice-rate 16000 --mel-rate 24000\n"
                 "  --mel-fft 1920 --mel-hop 480 --mel-power 1 --mel-floor 1e-5 --mel-centered off\n");
}

const char* need(int& i, int argc, char** argv, const char* name) {
    if (i + 1 >= argc) throw std::runtime_error(std::string(name) + " needs a value");
    return argv[++i];
}

bool on_off(const char* value, const char* name) {
    if (!std::strcmp(value, "on") || !std::strcmp(value, "1")) return true;
    if (!std::strcmp(value, "off") || !std::strcmp(value, "0")) return false;
    throw std::runtime_error(std::string(name) + " is on or off");
}
}

int main(int argc, char** argv) {
    std::vector<std::string> positional;
    int gpu = 0;
    int cond_seconds = -1;
    double lufs = -27;
    float trim_db = 20.f;
    int voice_seconds = 30;
    int prompt_seconds = 10;
    int mel_seconds = 10;
    int voice_rate = 16000;
    int mel_rate = 24000;
    int mel_fft = 1920;
    int mel_hop = 480;
    float mel_power = 1.f;
    float mel_floor = 1e-5f;
    bool mel_centered = false;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "-h" || a == "--help") {
            usage();
            return 0;
        } else if (a == "--gpu")
            gpu = std::atoi(need(i, argc, argv, "--gpu"));
        else if (a == "--cond-seconds")
            cond_seconds = std::atoi(need(i, argc, argv, "--cond-seconds"));
        else if (a == "--normalize-lufs")
            lufs = std::atof(need(i, argc, argv, "--normalize-lufs"));
        else if (a == "--trim-db")
            trim_db = std::atof(need(i, argc, argv, "--trim-db"));
        else if (a == "--voice-seconds")
            voice_seconds = std::atoi(need(i, argc, argv, "--voice-seconds"));
        else if (a == "--prompt-seconds")
            prompt_seconds = std::atoi(need(i, argc, argv, "--prompt-seconds"));
        else if (a == "--mel-seconds")
            mel_seconds = std::atoi(need(i, argc, argv, "--mel-seconds"));
        else if (a == "--voice-rate")
            voice_rate = std::atoi(need(i, argc, argv, "--voice-rate"));
        else if (a == "--mel-rate")
            mel_rate = std::atoi(need(i, argc, argv, "--mel-rate"));
        else if (a == "--mel-fft")
            mel_fft = std::atoi(need(i, argc, argv, "--mel-fft"));
        else if (a == "--mel-hop")
            mel_hop = std::atoi(need(i, argc, argv, "--mel-hop"));
        else if (a == "--mel-power")
            mel_power = std::atof(need(i, argc, argv, "--mel-power"));
        else if (a == "--mel-floor")
            mel_floor = std::atof(need(i, argc, argv, "--mel-floor"));
        else if (a == "--mel-centered")
            mel_centered = on_off(need(i, argc, argv, "--mel-centered"), "--mel-centered");
        else if (a[0] == '-')
            throw std::runtime_error("unknown argument: " + a);
        else
            positional.push_back(a);
    }
    if (positional.size() != 3) {
        usage();
        return 2;
    }
    const std::string t3_path = positional[0], s3_path = positional[1], reference_path = positional[2];
    auto family = trident::GgufFile(t3_path).string("general.architecture");
    if (family != "chatterbox-gpt2" && family != "chatterbox-llama")
        throw std::runtime_error("Unsupported architecture: " + family);
    int seconds = cond_seconds >= 0 ? cond_seconds : (family == "chatterbox-gpt2" ? 15 : 6);
    trident::VulkanBackend backend(gpu);
    trident::Audio reference(reference_path);
    auto normalized = reference;
    normalized.normalize(lufs);
    auto at16 = normalized.resample(voice_rate);
    auto voice_audio = at16;
    voice_audio.trim(trim_db).take_seconds(voice_seconds);
    std::vector<float> speaker;
    { trident::VoiceEncoder encoder(t3_path, backend); speaker = encoder.embed(voice_audio); }
    uint32_t maximum = trident::GgufFile(t3_path).u32("chatterbox.cond_prompt_max");
    auto prompt_audio = at16;
    prompt_audio.take_seconds(prompt_seconds);
    auto condition_audio = at16;
    condition_audio.take_seconds(seconds);
    std::vector<int32_t> prompt, condition;
    {
        trident::S3Tokenizer tokenizer(s3_path, backend);
        prompt = tokenizer.tokenize(prompt_audio, -1);
        condition = tokenizer.tokenize(condition_audio, int(maximum));
    }
    auto at24 = reference.resample(mel_rate);
    at24.normalize(lufs).take_seconds(mel_seconds);
    trident::GgufFile s3(s3_path);
    int mels = int(s3.tensor("s3gen/mel_fb/24k_80")->ne[1]);
    auto features = at24.mel(s3.floats("s3gen/mel_fb/24k_80"), backend, mel_fft, mel_hop, mels, mel_centered, mel_power, mel_floor);
    std::vector<float> embedding;
    { trident::CampPlus camp(s3_path, backend); embedding = camp.embed(prompt_audio); }
    trident::GgufFile(t3_path).rewrite(t3_path, {
        {"chatterbox/builtin/speaker_emb", GGML_TYPE_F32, {int64_t(speaker.size()), 1}, speaker.data()},
        {"chatterbox/builtin/cond_prompt_speech_tokens", GGML_TYPE_I32, {int64_t(condition.size())}, condition.data()},
    }, {{"chatterbox.cond_prompt_max", maximum}});
    trident::GgufFile(s3_path).rewrite(s3_path, {
        {"s3gen/builtin/prompt_token", GGML_TYPE_I32, {int64_t(prompt.size())}, prompt.data()},
        {"s3gen/builtin/prompt_feat", GGML_TYPE_F32, {mels, int64_t(features.size() / mels)}, features.data()},
        {"s3gen/builtin/embedding", GGML_TYPE_F32, {int64_t(embedding.size())}, embedding.data()},
    }, {});
}
