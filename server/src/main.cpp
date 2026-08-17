#include "server.hpp"
#include <chrono>
#include <filesystem>
#include <iostream>
#include <string>
#include <thread>

int main(int argc, char** argv) {
    int port = 0, gpu = 0, context = 0, sessions = 0, threads = 0;
    std::string t3, s3;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (i + 1 >= argc) {
            std::cerr << "missing value for " << arg << std::endl;
            return 2;
        }
        if (arg == "--port") port = std::stoi(argv[++i]);
        else if (arg == "--model") t3 = argv[++i];
        else if (arg == "--s3gen-gguf") s3 = argv[++i];
        else if (arg == "--n-gpu-layers") gpu = std::stoi(argv[++i]);
        else if (arg == "--context") context = std::stoi(argv[++i]);
        else if (arg == "--max-sessions") sessions = std::stoi(argv[++i]);
        else if (arg == "--threads") threads = std::stoi(argv[++i]);
        else {
            std::cerr << "unknown argument: " << arg << std::endl;
            return 2;
        }
    }
    if (port < 1 || gpu < 1 || context < 1 || sessions < 1 || threads < 1 || !std::filesystem::is_regular_file(t3) || !std::filesystem::is_regular_file(s3)) {
        std::cerr << "invalid TTS configuration" << std::endl;
        return 2;
    }
    try {
        tts::TTSServer server(port);
        server.initialize(t3, s3, gpu, threads, context, sessions);
        server.start();
        std::cout << "TTS Vulkan ws://127.0.0.1:" << port << "/tts" << std::endl;
        for (;;) std::this_thread::sleep_for(std::chrono::hours(24));
    } catch (const std::exception& exception) {
        std::cerr << exception.what() << std::endl;
        return 1;
    }
}
