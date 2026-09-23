#pragma once
#include "bus.hpp"
#include <string>
#include <vector>
#include <windows.h>

namespace trident::host {
class Supervisor {
    Bus& bus_;
    std::string variant_;
    bool inbox_only_;
    std::vector<std::pair<std::string, PROCESS_INFORMATION>> workers_;

public:
    Supervisor(Bus& bus, std::string variant, bool inbox_only);
    ~Supervisor();
    void start(int port);
    void run();

private:
    static void terminate_all(std::vector<std::pair<std::string, PROCESS_INFORMATION>>& workers);
    void drain_speech();
    static bool speech_pending();
};
}
