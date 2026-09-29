#pragma once
#include <string>
#include <vector>

namespace trident {
class CaptureDevice {
public:
    int native_rate = 0;
    static CaptureDevice open(const std::string& device);
    void read(float* dst, int frames);
    void close();
    CaptureDevice() = default;
    CaptureDevice(CaptureDevice&& other) noexcept;
    CaptureDevice& operator=(CaptureDevice&& other) noexcept;
    ~CaptureDevice();
    CaptureDevice(const CaptureDevice&) = delete;
    CaptureDevice& operator=(const CaptureDevice&) = delete;
private:
    struct Impl;
    Impl* impl = nullptr;
};
}
