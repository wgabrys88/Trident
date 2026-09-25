#include "silero_vad.h"
#include "config.h"
#include <onnxruntime_c_api.h>
#include <cctype>
#include <cstring>
#include <string>
#include <vector>

namespace trident {
struct SileroVad::Impl {
    const OrtApi* api = nullptr;
    OrtEnv* env = nullptr;
    OrtSession* session = nullptr;
    OrtAllocator* allocator = nullptr;
    std::string audio_name, state_name, rate_name, out_name, state_out_name;
    std::vector<float> state;
    int64_t state_shape[3] = {2, 1, 128};
    int window = 512;
    int rate = 16000;
    float threshold = 0.5f;
    int min_silence_samples = 0;
    int speech_pad_samples = 0;
    bool triggered = false;
    int temp_end = 0;
    int current_sample = 0;
};

static void ort_fail(const OrtApi* api, OrtStatus* status, const char* what) {
    if (!status) return;
    std::string text = what;
    text += ": ";
    text += api->GetErrorMessage(status);
    api->ReleaseStatus(status);
    fail(text);
}

static std::string ort_name(const OrtApi* api, OrtAllocator* allocator, char* raw) {
    std::string name = raw ? raw : "";
    allocator->Free(allocator, raw);
    return name;
}

SileroVad::SileroVad(const std::filesystem::path& onnx, int rate, int window, float threshold, int min_silence_ms, int speech_pad_ms) {
    impl = new Impl;
    impl->window = window;
    impl->rate = rate;
    impl->threshold = threshold;
    impl->min_silence_samples = rate * min_silence_ms / 1000;
    impl->speech_pad_samples = rate * speech_pad_ms / 1000;
    impl->api = OrtGetApiBase()->GetApi(ORT_API_VERSION);
    if (!impl->api) fail("ONNX Runtime API missing");
    ort_fail(impl->api, impl->api->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "vad", &impl->env), "onnx env");
    OrtSessionOptions* options = nullptr;
    ort_fail(impl->api, impl->api->CreateSessionOptions(&options), "onnx options");
    ort_fail(impl->api, impl->api->CreateSession(impl->env, onnx.c_str(), options, &impl->session), "onnx session");
    impl->api->ReleaseSessionOptions(options);
    ort_fail(impl->api, impl->api->GetAllocatorWithDefaultOptions(&impl->allocator), "onnx allocator");
    size_t inputs = 0;
    ort_fail(impl->api, impl->api->SessionGetInputCount(impl->session, &inputs), "onnx inputs");
    for (size_t i = 0; i < inputs; ++i) {
        char* raw = nullptr;
        ort_fail(impl->api, impl->api->SessionGetInputName(impl->session, i, impl->allocator, &raw), "onnx input name");
        std::string name = ort_name(impl->api, impl->allocator, raw);
        std::string lower = name;
        for (char& c : lower) c = (char)std::tolower((unsigned char)c);
        if (lower.find("sr") != std::string::npos || lower.find("sample") != std::string::npos) impl->rate_name = name;
        else if (lower.find("state") != std::string::npos || lower == "h" || lower == "c") {
            if (impl->state_name.empty()) impl->state_name = name;
        } else impl->audio_name = name;
    }
    size_t outputs = 0;
    ort_fail(impl->api, impl->api->SessionGetOutputCount(impl->session, &outputs), "onnx outputs");
    for (size_t i = 0; i < outputs; ++i) {
        char* raw = nullptr;
        ort_fail(impl->api, impl->api->SessionGetOutputName(impl->session, i, impl->allocator, &raw), "onnx output name");
        std::string name = ort_name(impl->api, impl->allocator, raw);
        std::string lower = name;
        for (char& c : lower) c = (char)std::tolower((unsigned char)c);
        if (lower.find("state") != std::string::npos) impl->state_out_name = name;
        else if (impl->out_name.empty()) impl->out_name = name;
    }
    if (impl->audio_name.empty() || impl->state_name.empty() || impl->rate_name.empty() || impl->out_name.empty() || impl->state_out_name.empty())
        fail("silero onnx inputs are not input/state/sr");
    impl->state.assign(2 * 128, 0.f);
}

SileroVad::~SileroVad() {
    if (!impl) return;
    if (impl->session) impl->api->ReleaseSession(impl->session);
    if (impl->env) impl->api->ReleaseEnv(impl->env);
    delete impl;
}

void SileroVad::reset() {
    std::fill(impl->state.begin(), impl->state.end(), 0.f);
    impl->triggered = false;
    impl->temp_end = 0;
    impl->current_sample = 0;
}

VadEvent SileroVad::feed(const float* samples, int count) {
    if (count != impl->window) fail("silero frame is not vad.window");
    std::vector<float> audio_copy(samples, samples + count);
    int64_t audio_shape[2] = {1, count};
    int64_t rate_shape[1] = {1};
    int64_t rate_value = impl->rate;
    OrtMemoryInfo* memory = nullptr;
    ort_fail(impl->api, impl->api->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault, &memory), "onnx memory");
    OrtValue* audio = nullptr;
    OrtValue* state = nullptr;
    OrtValue* sr = nullptr;
    ort_fail(impl->api, impl->api->CreateTensorWithDataAsOrtValue(memory, audio_copy.data(), sizeof(float) * audio_copy.size(), audio_shape, 2, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &audio), "onnx audio");
    ort_fail(impl->api, impl->api->CreateTensorWithDataAsOrtValue(memory, impl->state.data(), sizeof(float) * impl->state.size(), impl->state_shape, 3, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &state), "onnx state");
    ort_fail(impl->api, impl->api->CreateTensorWithDataAsOrtValue(memory, &rate_value, sizeof(rate_value), rate_shape, 1, ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64, &sr), "onnx sr");
    const char* in_names[3] = {impl->audio_name.c_str(), impl->state_name.c_str(), impl->rate_name.c_str()};
    OrtValue* in_values[3] = {audio, state, sr};
    const char* out_names[2] = {impl->out_name.c_str(), impl->state_out_name.c_str()};
    OrtValue* out_values[2] = {nullptr, nullptr};
    ort_fail(impl->api, impl->api->Run(impl->session, nullptr, in_names, in_values, 3, out_names, 2, out_values), "onnx run");
    float* prob = nullptr;
    float* next = nullptr;
    ort_fail(impl->api, impl->api->GetTensorMutableData(out_values[0], (void**)&prob), "onnx prob");
    ort_fail(impl->api, impl->api->GetTensorMutableData(out_values[1], (void**)&next), "onnx next state");
    std::memcpy(impl->state.data(), next, sizeof(float) * impl->state.size());
    float speech = prob[0];
    impl->api->ReleaseValue(out_values[0]);
    impl->api->ReleaseValue(out_values[1]);
    impl->api->ReleaseValue(audio);
    impl->api->ReleaseValue(state);
    impl->api->ReleaseValue(sr);
    impl->api->ReleaseMemoryInfo(memory);
    impl->current_sample += count;
    VadEvent event;
    if (speech >= impl->threshold && impl->temp_end) impl->temp_end = 0;
    if (speech >= impl->threshold && !impl->triggered) {
        impl->triggered = true;
        event.start = true;
        return event;
    }
    if (speech < impl->threshold - 0.15f && impl->triggered) {
        if (!impl->temp_end) impl->temp_end = impl->current_sample;
        if (impl->current_sample - impl->temp_end >= impl->min_silence_samples) {
            impl->temp_end = 0;
            impl->triggered = false;
            event.end = true;
        }
    }
    return event;
}
}
