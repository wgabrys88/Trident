#include "wasapi_capture.h"
#include "config.h"
#include <audioclient.h>
#include <audioclientactivationparams.h>
#include <ksmedia.h>
#include <mmdeviceapi.h>
#include <mmreg.h>
#include <propvarutil.h>
#include <objbase.h>
#include <cstdlib>
#include <filesystem>
#include <vector>

namespace trident {
static const PROPERTYKEY device_friendly_name = {
    { 0xa45c254e, 0xdf1c, 0x4efd, { 0x80, 0x20, 0x67, 0xd1, 0x46, 0xa8, 0x50, 0xe0 } },
    14
};

struct CaptureDevice::Impl {
    IMMDeviceEnumerator* enumerator = nullptr;
    IAudioClient* client = nullptr;
    IAudioCaptureClient* capture = nullptr;
    WAVEFORMATEX* mix = nullptr;
    std::vector<float> pending;
};

static std::string friendly_name(IMMDevice* device) {
    IPropertyStore* store = nullptr;
    if (FAILED(device->OpenPropertyStore(STGM_READ, &store))) return {};
    PROPVARIANT value;
    PropVariantInit(&value);
    std::string name;
    if (SUCCEEDED(store->GetValue(device_friendly_name, &value)) && value.vt == VT_LPWSTR && value.pwszVal)
        name = path_u8(std::filesystem::path(value.pwszVal));
    PropVariantClear(&value);
    store->Release();
    return name;
}

CaptureDevice CaptureDevice::open_loopback(const std::string& pid_text) {
    char* end = nullptr;
    unsigned long pid = std::strtoul(pid_text.c_str(), &end, 10);
    if (end == pid_text.c_str() || *end != 0 || pid == 0) fail("loopback pid");
    HANDLE proc = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!proc) fail("loopback process absent");
    CloseHandle(proc);
    if (FAILED(CoInitializeEx(nullptr, COINIT_MULTITHREADED))) fail("WASAPI init failed");
    struct Handler : IActivateAudioInterfaceCompletionHandler, IAgileObject {
        LONG refs = 1;
        HANDLE event = nullptr;
        HRESULT hr = E_FAIL;
        IAudioClient* client = nullptr;
        Handler() { event = CreateEventW(nullptr, TRUE, FALSE, nullptr); }
        ~Handler() {
            if (client) client->Release();
            if (event) CloseHandle(event);
        }
        HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void** out) override {
            if (!out) return E_POINTER;
            if (id == __uuidof(IUnknown) || id == __uuidof(IActivateAudioInterfaceCompletionHandler)) {
                *out = static_cast<IActivateAudioInterfaceCompletionHandler*>(this);
                AddRef();
                return S_OK;
            }
            if (id == __uuidof(IAgileObject)) {
                *out = static_cast<IAgileObject*>(this);
                AddRef();
                return S_OK;
            }
            *out = nullptr;
            return E_NOINTERFACE;
        }
        ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&refs); }
        ULONG STDMETHODCALLTYPE Release() override {
            ULONG n = InterlockedDecrement(&refs);
            if (!n) delete this;
            return n;
        }
        HRESULT STDMETHODCALLTYPE ActivateCompleted(IActivateAudioInterfaceAsyncOperation* op) override {
            IUnknown* unk = nullptr;
            HRESULT activated = E_FAIL;
            hr = op->GetActivateResult(&activated, &unk);
            if (SUCCEEDED(hr)) hr = activated;
            if (SUCCEEDED(hr) && unk) hr = unk->QueryInterface(__uuidof(IAudioClient), (void**)&client);
            if (unk) unk->Release();
            SetEvent(event);
            return S_OK;
        }
    };
    Handler* handler = new Handler();
    if (!handler->event) fail("loopback event");
    AUDIOCLIENT_ACTIVATION_PARAMS params{};
    params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
    params.ProcessLoopbackParams.TargetProcessId = pid;
    params.ProcessLoopbackParams.ProcessLoopbackMode = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;
    PROPVARIANT activate;
    PropVariantInit(&activate);
    activate.vt = VT_BLOB;
    activate.blob.cbSize = sizeof(params);
    activate.blob.pBlobData = reinterpret_cast<BYTE*>(&params);
    IActivateAudioInterfaceAsyncOperation* async_op = nullptr;
    HRESULT hr = ActivateAudioInterfaceAsync(
        VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, __uuidof(IAudioClient), &activate, handler, &async_op);
    if (FAILED(hr)) fail("loopback activate failed");
    if (WaitForSingleObject(handler->event, 15000) != WAIT_OBJECT_0) fail("loopback activate failed");
    hr = handler->hr;
    IAudioClient* client = handler->client;
    handler->client = nullptr;
    if (async_op) async_op->Release();
    handler->Release();
    if (FAILED(hr) || !client) fail("loopback activate failed");
    CaptureDevice out;
    out.impl = new Impl;
    out.impl->client = client;
    WAVEFORMATEX* format = (WAVEFORMATEX*)CoTaskMemAlloc(sizeof(WAVEFORMATEX));
    if (!format) fail("WASAPI initialize failed");
    format->wFormatTag = WAVE_FORMAT_PCM;
    format->nChannels = 2;
    format->nSamplesPerSec = 44100;
    format->wBitsPerSample = 16;
    format->nBlockAlign = 4;
    format->nAvgBytesPerSec = 44100 * 4;
    format->cbSize = 0;
    out.impl->mix = format;
    out.native_rate = 44100;
    if (FAILED(out.impl->client->Initialize(
            AUDCLNT_SHAREMODE_SHARED,
            AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM,
            0,
            0,
            format,
            nullptr)))
        fail("WASAPI initialize failed");
    if (FAILED(out.impl->client->GetService(__uuidof(IAudioCaptureClient), (void**)&out.impl->capture)))
        fail("WASAPI capture client failed");
    if (FAILED(out.impl->client->Start())) fail("WASAPI start failed");
    return out;
}

CaptureDevice CaptureDevice::open(const std::string& device) {
    const std::string loop = "loopback:";
    if (device.rfind(loop, 0) == 0) return open_loopback(device.substr(loop.size()));
    CaptureDevice out;
    out.impl = new Impl;
    if (FAILED(CoInitializeEx(nullptr, COINIT_MULTITHREADED))) fail("WASAPI init failed");
    if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, __uuidof(IMMDeviceEnumerator), (void**)&out.impl->enumerator)))
        fail("WASAPI enumerator failed");
    IMMDeviceCollection* devices = nullptr;
    if (FAILED(out.impl->enumerator->EnumAudioEndpoints(eCapture, DEVICE_STATE_ACTIVE, &devices)))
        fail("WASAPI capture list failed");
    UINT count = 0;
    devices->GetCount(&count);
    IMMDevice* chosen = nullptr;
    for (UINT i = 0; i < count; ++i) {
        IMMDevice* item = nullptr;
        devices->Item(i, &item);
        if (friendly_name(item) == device) {
            chosen = item;
            break;
        }
        item->Release();
    }
    devices->Release();
    if (!chosen) fail("vad.device capture endpoint was not found: " + device);
    if (FAILED(chosen->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr, (void**)&out.impl->client)))
        fail("WASAPI audio client failed");
    chosen->Release();
    if (FAILED(out.impl->client->GetMixFormat(&out.impl->mix))) fail("WASAPI mix format failed");
    out.native_rate = (int)out.impl->mix->nSamplesPerSec;
    if (FAILED(out.impl->client->Initialize(AUDCLNT_SHAREMODE_SHARED, 0, 10000000, 0, out.impl->mix, nullptr)))
        fail("WASAPI initialize failed");
    if (FAILED(out.impl->client->GetService(__uuidof(IAudioCaptureClient), (void**)&out.impl->capture)))
        fail("WASAPI capture client failed");
    if (FAILED(out.impl->client->Start())) fail("WASAPI start failed");
    return out;
}

void CaptureDevice::read(float* dst, int frames) {
    int filled = 0;
    while (filled < frames) {
        if (!impl->pending.empty()) {
            int n = (int)impl->pending.size();
            if (n > frames - filled) n = frames - filled;
            std::copy(impl->pending.begin(), impl->pending.begin() + n, dst + filled);
            impl->pending.erase(impl->pending.begin(), impl->pending.begin() + n);
            filled += n;
            continue;
        }
        UINT32 packet = 0;
        if (FAILED(impl->capture->GetNextPacketSize(&packet))) fail("WASAPI packet failed");
        if (!packet) {
            Sleep(5);
            continue;
        }
        BYTE* data = nullptr;
        UINT32 count = 0;
        DWORD flags = 0;
        if (FAILED(impl->capture->GetBuffer(&data, &count, &flags, nullptr, nullptr))) fail("WASAPI buffer failed");
        const int channels = impl->mix->nChannels;
        const bool silent = flags & AUDCLNT_BUFFERFLAGS_SILENT;
        bool pcm16 = impl->mix->wFormatTag == WAVE_FORMAT_PCM && impl->mix->wBitsPerSample == 16;
        bool is_float = impl->mix->wFormatTag == WAVE_FORMAT_IEEE_FLOAT;
        if (impl->mix->wFormatTag == WAVE_FORMAT_EXTENSIBLE) {
            auto* ext = (WAVEFORMATEXTENSIBLE*)impl->mix;
            is_float = ext->SubFormat == KSDATAFORMAT_SUBTYPE_IEEE_FLOAT;
            pcm16 = ext->SubFormat == KSDATAFORMAT_SUBTYPE_PCM && impl->mix->wBitsPerSample == 16;
        }
        for (UINT32 i = 0; i < count; ++i) {
            float sum = 0;
            for (int c = 0; c < channels; ++c) {
                float sample = 0;
                if (!silent && pcm16) sample = ((int16_t*)data)[i * channels + c] / 32768.f;
                else if (!silent && is_float) sample = ((float*)data)[i * channels + c];
                sum += sample;
            }
            impl->pending.push_back(channels ? sum / channels : 0);
        }
        impl->capture->ReleaseBuffer(count);
    }
}

void CaptureDevice::close() {
    if (!impl) return;
    if (impl->client) impl->client->Stop();
    if (impl->capture) impl->capture->Release();
    if (impl->client) impl->client->Release();
    if (impl->mix) CoTaskMemFree(impl->mix);
    if (impl->enumerator) impl->enumerator->Release();
    delete impl;
    impl = nullptr;
}

CaptureDevice::CaptureDevice(CaptureDevice&& other) noexcept : native_rate(other.native_rate), impl(other.impl) {
    other.impl = nullptr;
    other.native_rate = 0;
}

CaptureDevice& CaptureDevice::operator=(CaptureDevice&& other) noexcept {
    if (this != &other) {
        close();
        impl = other.impl;
        native_rate = other.native_rate;
        other.impl = nullptr;
        other.native_rate = 0;
    }
    return *this;
}

CaptureDevice::~CaptureDevice() { close(); }
}
