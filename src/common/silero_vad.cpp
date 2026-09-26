#include "silero_vad.h"
#include "config.h"
#include <onnxruntime_c_api.h>
#include <algorithm>
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
    std::vector<float> context;
    int64_t state_shape[3] = {2, 1, 128};
    int window = 512;
    int rate = 16000;
    float threshold = 0.5f;
    int min_silence_samples = 0;
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

static bool whole_name(const std::string& seen, const char* name) {
    const std::string token = std::string(" ") + name + " ";
    return (" " + seen + " ").find(token) != std::string::npos;
}

static std::string session_names(const OrtApi* api, OrtSession* session, OrtAllocator* allocator, bool input, size_t count) {
    std::string seen;
    for (size_t i = 0; i < count; ++i) {
        char* raw = nullptr;
        OrtStatus* status = input
            ? api->SessionGetInputName(session, i, allocator, &raw)
            : api->SessionGetOutputName(session, i, allocator, &raw);
        ort_fail(api, status, input ? "onnx input name" : "onnx output name");
        if (!seen.empty()) seen.push_back(' ');
        seen += ort_name(api, allocator, raw);
    }
    return seen;
}

SileroVad::SileroVad(const std::filesystem::path& onnx, int rate, int window, float threshold, int min_silence_ms) {
    impl = new Impl;
    impl->window = window;
    impl->rate = rate;
    impl->threshold = threshold;
    impl->min_silence_samples = rate * min_silence_ms / 1000;
    impl->context.assign(window / 8, 0.f);
    impl->api = OrtGetApiBase()->GetApi(ORT_API_VERSION);
    if (!impl->api) fail("ONNX Runtime API missing");
    ort_fail(impl->api, impl->api->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "vad", &impl->env), "onnx env");
    OrtSessionOptions* options = nullptr;
    ort_fail(impl->api, impl->api->CreateSessionOptions(&options), "onnx options");
    ort_fail(impl->api, impl->api->CreateSession(impl->env, onnx.c_str(), options, &impl->session), "onnx session");
    impl->api->ReleaseSessionOptions(options);
    ort_fail(impl->api, impl->api->GetAllocatorWithDefaultOptions(&impl->allocator), "onnx allocator");
    size_t inputs = 0;
    size_t outputs = 0;
    ort_fail(impl->api, impl->api->SessionGetInputCount(impl->session, &inputs), "onnx inputs");
    ort_fail(impl->api, impl->api->SessionGetOutputCount(impl->session, &outputs), "onnx outputs");
    const std::string in_seen = session_names(impl->api, impl->session, impl->allocator, true, inputs);
    const std::string out_seen = session_names(impl->api, impl->session, impl->allocator, false, outputs);
    if (inputs != 3 || outputs != 2 || !whole_name(in_seen, "input") || !whole_name(in_seen, "state") || !whole_name(in_seen, "sr") || !whole_name(out_seen, "output") || !whole_name(out_seen, "stateN"))
        fail("silero onnx inputs are not input/state/sr " + in_seen + " " + out_seen);
    impl->audio_name = "input";
    impl->state_name = "state";
    impl->rate_name = "sr";
    impl->out_name = "output";
    impl->state_out_name = "stateN";
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
    std::fill(impl->context.begin(), impl->context.end(), 0.f);
    impl->triggered = false;
    impl->temp_end = 0;
    impl->current_sample = 0;
}

VadEvent SileroVad::feed(const float* samples, int count) {
    if (count != impl->window) fail("silero frame is not vad.window");
    std::vector<float> audio_copy;
    audio_copy.reserve(impl->context.size() + count);
    audio_copy.insert(audio_copy.end(), impl->context.begin(), impl->context.end());
    audio_copy.insert(audio_copy.end(), samples, samples + count);
    int64_t audio_shape[2] = {1, (int64_t)audio_copy.size()};
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
    std::copy(audio_copy.end() - impl->context.size(), audio_copy.end(), impl->context.begin());
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
