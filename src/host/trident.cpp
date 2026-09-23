#include "bus.hpp"
#include "devices.hpp"
#include "paths.hpp"
#include "supervisor.hpp"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <thread>

int main(int argc, char** argv) {
    std::string variant = "turbo";
    bool inbox_only = false;
    bool server_only = false;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--server-only") server_only = true;
        else if (arg == "inbox") inbox_only = true;
        else if (arg == "nano" || arg == "turbo" || arg == "v3") variant = arg;
        else {
            fprintf(stderr, "usage: trident-host [nano|turbo|v3] [inbox] [--server-only]\n");
            return 2;
        }
    }
    int port = 8765;
    if (const char* env = std::getenv("TRIDENT_PORT")) port = std::stoi(env);
    int idle = 1800;
    if (const char* env = std::getenv("TRIDENT_IDLE_SECONDS")) idle = std::stoi(env);
    int device = trident::host::pick_vulkan_device();
    fprintf(stderr, "vulkan device %d\n", device);

    trident::host::Bus bus(variant, idle, device);
    std::thread server([&] {
        try {
            bus.run(port);
        } catch (const std::exception& err) {
            fprintf(stderr, "bus error %s\n", err.what());
        }
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(200));

    try {
        if (!server_only) {
            trident::host::Supervisor sup(bus, variant, inbox_only);
            sup.start(port);
            sup.run();
        } else {
            while (!bus.stopped()) std::this_thread::sleep_for(std::chrono::seconds(1));
        }
    } catch (...) {
        bus.shutdown();
        server.join();
        throw;
    }
    bus.shutdown();
    server.join();
    return 0;
}
