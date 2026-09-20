#pragma once
#include "audio.h"
#include "voice_encoder.h"
#include "campplus.h"
#include "s3_tokenizer.h"

namespace trident {
inline void bake_voice(const std::string& t3_path, const std::string& s3_path, const std::string& reference_path, int condition_seconds) {
    VulkanBackend backend(0);
    Audio reference(reference_path);
    auto normalized = reference;
    normalized.normalize();
    auto at16 = normalized.resample(16000);
    auto voice_audio = at16;
    voice_audio.trim().take_seconds(30);
    std::vector<float> speaker;
    { VoiceEncoder encoder(t3_path, backend); speaker = encoder.embed(voice_audio); }
    uint32_t maximum = GgufFile(t3_path).u32("chatterbox.cond_prompt_max");
    auto prompt_audio = at16;
    prompt_audio.take_seconds(10);
    auto condition_audio = at16;
    condition_audio.take_seconds(condition_seconds);
    std::vector<int32_t> prompt, condition;
    {
        S3Tokenizer tokenizer(s3_path, backend);
        prompt = tokenizer.tokenize(prompt_audio, -1);
        condition = tokenizer.tokenize(condition_audio, int(maximum));
    }
    auto at24 = reference.resample(24000);
    at24.normalize().take_seconds(10);
    GgufFile s3(s3_path);
    int mels = int(s3.tensor("s3gen/mel_fb/24k_80")->ne[1]);
    auto features = at24.mel(s3.floats("s3gen/mel_fb/24k_80"), backend, 1920, 480, mels, false, 1.f, 1e-5f);
    std::vector<float> embedding;
    { CampPlus camp(s3_path, backend); embedding = camp.embed(prompt_audio); }
    GgufFile(t3_path).rewrite(t3_path, {
        {"chatterbox/builtin/speaker_emb", GGML_TYPE_F32, {int64_t(speaker.size()), 1}, speaker.data()},
        {"chatterbox/builtin/cond_prompt_speech_tokens", GGML_TYPE_I32, {int64_t(condition.size())}, condition.data()},
    }, {{"chatterbox.cond_prompt_max", maximum}});
    GgufFile(s3_path).rewrite(s3_path, {
        {"s3gen/builtin/prompt_token", GGML_TYPE_I32, {int64_t(prompt.size())}, prompt.data()},
        {"s3gen/builtin/prompt_feat", GGML_TYPE_F32, {mels, int64_t(features.size() / mels)}, features.data()},
        {"s3gen/builtin/embedding", GGML_TYPE_F32, {int64_t(embedding.size())}, embedding.data()},
    }, {});
}
}
