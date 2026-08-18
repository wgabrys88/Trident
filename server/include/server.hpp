#pragma once
#include "engine_wrapper.hpp"
#include "httplib.h"
#include <atomic>
#include <memory>
#include <thread>

namespace tts {
class TTSServer {
public:
    explicit TTSServer(int);
    ~TTSServer();
    void initialize(const std::string&, const std::string&, int, int, int);
    void start();
    void stop();
private:
    int port_;
    std::atomic<bool> running_{false};
    std::unique_ptr<EngineWrapper> engine_;
    httplib::Server http_;
    std::thread thread_;
};
}
