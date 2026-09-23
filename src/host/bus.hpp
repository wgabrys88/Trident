#pragma once
#include "journal.hpp"
#include "store.hpp"
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <string>
#include <thread>

namespace httplib {
class Server;
}

namespace trident::host {
class Bus {
    Store store_;
    Journal journal_;
    std::filesystem::path events_log_;
    std::string variant_;
    int idle_seconds_;
    int vulkan_device_;
    std::atomic<bool> stop_{false};
    double last_clock_ = 0;
    std::thread timer_;
    mutable std::mutex speech_mutex_;
    std::condition_variable speech_changed_;
    httplib::Server* server_ = nullptr;

public:
    Bus(std::string variant, int idle_seconds, int vulkan_device);
    ~Bus();
    void run(int port);
    void shutdown();
    bool stopped() const { return stop_; }
    void set_stop();
    Store& store() { return store_; }

private:
    void timer_loop();
    json next_speech(double timeout_sec);
};
}
