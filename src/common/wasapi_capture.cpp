#include "wasapi_capture.h"
#include "config.h"
#include <filesystem>
#include <audioclient.h>
#include <ksmedia.h>
#include <cctype>
#include <functiondiscoverykeys_devpkey.h>
#include <mmdeviceapi.h>
#include <propvarutil.h>
#include <vector>

namespace trident {
struct CaptureDevice::Impl {
    IMMDeviceEnumerator* enumerator = nullptr;
    IAudioClient* client = nullptr;
    IAudioCaptureClient* capture = nullptr;
    WAVEFORMATEX* mix = nullptr;
    std::vector<float> pending;
};

static std::string lower_copy(std::string text) {
    for (char& c : text) c = (char)std::tolower((unsigned char)c);
    return text;
}

static bool name_matches(const std::string& query, const std::string& friendly) {
    const std::string q = lower_copy(query);
    const std::string name = lower_copy(friendly);
    if (q == name) return true;
    if (q.find(name) != std::string::npos) return true;
    std::string host = name + ", windows wasapi";
    return q == host;
}

static std::string friendly_name(IMMDevice* device) {
    IPropertyStore* store = nullptr;
    if (FAILED(device->OpenPropertyStore(STGM_READ, &store))) return {};
    PROPVARIANT value;
    PropVariantInit(&value);
    std::string name;
    if (SUCCEEDED(store->GetValue(PKEY_Device_FriendlyName, &value)) && value.vt == VT_LPWSTR && value.pwszVal)
        name = path_u8(std::filesystem::path(value.pwszVal));
    PropVariantClear(&value);
    store->Release();
    return name;
}

CaptureDevice CaptureDevice::open(const std::string& device) {
    CaptureDevice out;
    out.impl = new Impl;
    if (FAILED(CoInitializeEx(nullptr, COINIT_MULTITHREADED)) && FAILED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED)))
        fail("WASAPI init failed");
    if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, __uuidof(IMMDeviceEnumerator), (void**)&out.impl->enumerator)))
        fail("WASAPI enumerator failed");
    IMMDeviceCollection* devices = nullptr;
    if (FAILED(out.impl->enumerator->EnumAudioEndpoints(eCapture, DEVICE_STATE_ACTIVE, &devices)))
        fail("WASAPI capture list failed");
    UINT count = 0;
    devices->GetCount(&count);
    IMMDevice* chosen = nullptr;
    bool digits = !device.empty();
    for (char c : device) if (c < '0' || c > '9') digits = false;
    if (digits) {
        unsigned index = (unsigned)std::stoul(device);
        if (index >= count) fail("vad.device index is outside the capture list");
        devices->Item(index, &chosen);
    } else {
        for (UINT i = 0; i < count; ++i) {
            IMMDevice* item = nullptr;
            devices->Item(i, &item);
            if (name_matches(device, friendly_name(item))) {
                chosen = item;
                break;
            }
            item->Release();
        }
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
