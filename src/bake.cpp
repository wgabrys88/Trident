#include "common/audio.h"
#include "common/voice_encoder.h"
#include "common/campplus.h"
#include "common/s3_tokenizer.h"
#include "common/gguf_file.h"
#include "common/config.h"
#include <stdexcept>
#include <string>
#include <vector>

int main(int argc, char** argv) {
    const auto values = trident::load_trident();
    const int gpu = trident::cfg_int(values, "bake.gpu", 0);
    const int cond_seconds = trident::cfg_int(values, "bake.cond-seconds", -1);
    const double lufs = trident::cfg_float(values, "bake.normalize-lufs", -27);
    const float trim_db = trident::cfg_float(values, "bake.trim-db", 20);
    const int voice_seconds = trident::cfg_int(values, "bake.voice-seconds", 30);
    const int prompt_seconds = trident::cfg_int(values, "bake.prompt-seconds", 10);
    const int mel_seconds = trident::cfg_int(values, "bake.mel-seconds", 10);
    const int voice_rate = trident::cfg_int(values, "bake.voice-rate", 16000);
    const int mel_rate = trident::cfg_int(values, "bake.mel-rate", 24000);
    const int mel_fft = trident::cfg_int(values, "bake.mel-fft", 1920);
    const int mel_hop = trident::cfg_int(values, "bake.mel-hop", 480);
    const float mel_power = trident::cfg_float(values, "bake.mel-power", 1);
    const float mel_floor = trident::cfg_float(values, "bake.mel-floor", 1e-5f);
    const bool mel_centered = trident::cfg_on(values, "bake.mel-centered");
    std::string t3_path, s3_path, reference_path;
    if (argc == 4) {
        t3_path = argv[1];
        s3_path = argv[2];
        reference_path = argv[3];
    } else if (argc == 1) {
        if (trident::cfg(values, "bake.t3").empty() || trident::cfg(values, "bake.s3").empty() || trident::cfg(values, "bake.reference").empty())
            throw std::runtime_error("bake.t3, bake.s3, and bake.reference are empty");
        t3_path = trident::cfg_path(values, "bake.t3").string();
        s3_path = trident::cfg_path(values, "bake.s3").string();
        reference_path = trident::cfg_path(values, "bake.reference").string();
    } else {
        throw std::runtime_error("chatterbox-bake takes the t3, s3, and reference paths, or no arguments");
    }
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
