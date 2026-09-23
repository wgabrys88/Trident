#include "common/chatterbox_runtime.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>

namespace {
void usage(const char* argv0) {
    std::fprintf(stderr,
                 "usage: %s <nano|turbo|v3> -t <text> [-o out.wav] [-l language] [--gpu N]\n"
                 "       %s <t3.gguf> <s3.gguf> -t <text> [-o out.wav] [-l language] [--gpu N]\n",
                 argv0, argv0);
}
} // namespace

int main(int argc, char** argv) {
    if (argc < 4) {
        usage(argv[0]);
        return 2;
    }

    std::string text;
    std::string language = "en";
    std::string out_path;
    int gpu = 0;
    if (const char* env = std::getenv("TRIDENT_VULKAN_DEVICE")) gpu = std::atoi(env);

    const std::string a1 = argv[1];
    const bool by_variant = (a1 == "nano" || a1 == "turbo" || a1 == "v3");
    int opt = by_variant ? 2 : 4;
    if (!by_variant && argc < 5) {
        usage(argv[0]);
        return 2;
    }

    for (int i = opt; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "-t" && i + 1 < argc)
            text = argv[++i];
        else if (a == "-l" && i + 1 < argc)
            language = argv[++i];
        else if (a == "-o" && i + 1 < argc)
            out_path = argv[++i];
        else if (a == "--gpu" && i + 1 < argc)
            gpu = std::atoi(argv[++i]);
        else if (a == "-h" || a == "--help") {
            usage(argv[0]);
            return 0;
        } else {
            std::fprintf(stderr, "unknown argument: %s\n", a.c_str());
            return 2;
        }
    }
    if (text.empty()) {
        usage(argv[0]);
        return 2;
    }

    try {
        auto t0 = std::chrono::steady_clock::now();
        std::unique_ptr<trident::Synth> engine;
        if (by_variant)
            engine = trident::chatterbox_make_engine(a1, gpu);
        else
            engine = trident::chatterbox_make_engine_paths(argv[1], argv[2], gpu);
        auto pcm = engine->synthesize(text, language);
        auto ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();

        if (out_path.empty()) {
            auto dir = trident::chatterbox_repo_root() / "wav";
            auto stamp = std::chrono::system_clock::now().time_since_epoch().count();
            out_path = (dir / (std::to_string(stamp) + ".wav")).string();
        }
        trident::chatterbox_write_wav(out_path, pcm);
        std::fprintf(stderr, "chatterbox ok | wall %.0f ms | audio %.1f s | %s\n", ms, double(pcm.size()) / 24000.0,
                     out_path.c_str());
        return 0;
    } catch (const std::exception& err) {
        std::fprintf(stderr, "chatterbox error: %s\n", err.what());
        return 1;
    }
}
