#include "common/chatterbox_runtime.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {
void usage(const char* argv0) {
    std::fprintf(stderr,
                 "usage: %s <nano|turbo|v3> -t <text> [-o out.wav] [-l language] [knobs]\n"
                 "       %s <t3.gguf> <s3.gguf> -t <text> [-o out.wav] [-l language] [knobs]\n"
                 "knobs, current defaults (gpt2 nano/turbo | llama v3):\n"
                 "  --gpu 0 --seed 42 --temperature 0.8 --repeat-penalty 1.2 --n-predict 1000\n"
                 "  --trim-fade-samples 480 --top-p 0.95|1.0 --cfm-steps 2|10\n"
                 "  gpt2: --top-k 1000\n"
                 "  v3: --min-p 0.05 --cfg-weight 0.5 --exaggeration 0.5 --cfm-cfg 0.7\n",
                 argv0, argv0);
}

bool knob(const std::string& name) {
    return name == "--seed" || name == "--temperature" || name == "--top-k" || name == "--top-p" ||
           name == "--repeat-penalty" || name == "--n-predict" || name == "--cfm-steps" ||
           name == "--trim-fade-samples" || name == "--min-p" || name == "--cfg-weight" ||
           name == "--exaggeration" || name == "--cfm-cfg";
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
    std::vector<std::pair<std::string, std::string>> overrides;
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
        else if (knob(a) && i + 1 < argc)
            overrides.emplace_back(a, argv[++i]);
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
            engine = trident::chatterbox_make_engine(a1, gpu, overrides);
        else
            engine = trident::chatterbox_make_engine_paths(argv[1], argv[2], gpu, overrides);
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
