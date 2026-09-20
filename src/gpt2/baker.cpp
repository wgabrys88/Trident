#include "trident/gpt2_engine.h"
#include "common/audio.h"
#include "common/voice_encoder.h"
#include "common/campplus.h"
#include "common/s3_tokenizer.h"

namespace trident::gpt2 {
Baker::Baker(std::string t3, std::string s3, std::string reference)
    : t3_(std::move(t3)), s3_(std::move(s3)), reference_(std::move(reference)) {}
void Baker::bake() {
    VulkanBackend backend(0);
    Audio reference(reference_);
    auto normalized = reference;
    normalized.normalize();
    auto at16 = normalized.resample(16000);
    auto voice_audio = at16;
    voice_audio.trim().take_seconds(30);
    std::vector<float> speaker;
    { VoiceEncoder encoder(t3_, backend); speaker = encoder.embed(voice_audio); }
    uint32_t maximum = GgufFile(t3_).u32("chatterbox.cond_prompt_max");
    auto prompt_audio = at16;
    prompt_audio.take_seconds(10);
    auto condition_audio = at16;
    condition_audio.take_seconds(15);
    std::vector<int32_t> prompt, condition;
    {
        S3Tokenizer tokenizer(s3_, backend);
        prompt = tokenizer.tokenize(prompt_audio, -1);
        condition = tokenizer.tokenize(condition_audio, int(maximum));
    }
    auto at24 = reference.resample(24000);
    at24.normalize().take_seconds(10);
    auto features = at24.mel(GgufFile(s3_).floats("s3gen/mel_fb/24k_80"), backend, 1920, 480, 80, false, 1.f, 1e-5f);
    std::vector<float> embedding;
    { CampPlus camp(s3_, backend); embedding = camp.embed(prompt_audio); }
    GgufFile(t3_).rewrite(t3_, {
        {"chatterbox/builtin/speaker_emb", GGML_TYPE_F32, {256, 1}, speaker.data()},
        {"chatterbox/builtin/cond_prompt_speech_tokens", GGML_TYPE_I32, {int64_t(condition.size())}, condition.data()},
    }, {{"chatterbox.cond_prompt_max", maximum}, {"chatterbox.cond_prompt_length", uint32_t(condition.size())}});
    GgufFile(s3_).rewrite(s3_, {
        {"s3gen/builtin/prompt_token", GGML_TYPE_I32, {int64_t(prompt.size())}, prompt.data()},
        {"s3gen/builtin/prompt_feat", GGML_TYPE_F32, {80, int64_t(features.size() / 80)}, features.data()},
        {"s3gen/builtin/embedding", GGML_TYPE_F32, {int64_t(embedding.size())}, embedding.data()},
    }, {{"s3gen.builtin.prompt_token_len", uint32_t(prompt.size())}, {"s3gen.builtin.prompt_feat_frames", uint32_t(features.size() / 80)}});
}
}
