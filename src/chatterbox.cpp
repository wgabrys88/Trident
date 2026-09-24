#include "common/chatterbox_runtime.h"
#include "common/config.h"
#include <filesystem>
#include <fstream>
#include <sstream>
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
                 "       %s --persist <nano|turbo|v3>\n"
                 "       %s --unload\n"
                 "       %s <t3.gguf> <s3.gguf> -t <text> [-o out.wav] [-l language] [knobs]\n"
                 "example: %s turbo -t \"hello\" -o wav\\hello.wav\n"
                 "Voices are models/voices/<nano|turbo|v3>/t3.gguf and s3.gguf under the repo root.\n"
                 "From build/bin that root is two directories up. Sample rate is 24000.\n"
                 "Nano and turbo are English only. v3 accepts -l.\n"
                 "  -t text\n"
                 "      Sentence to speak. Required.\n"
                 "  -o out.wav\n"
                 "      Output file. Default is <repo>/wav/<timestamp>.wav.\n"
                 "  -l language\n"
                 "      Language code. Default en. Nano and turbo reject anything else.\n"
                 "  --gpu 0\n"
                 "      Vulkan device index.\n"
                 "  --seed 42\n"
                 "      Sampler seed.\n"
                 "  --temperature 0.8\n"
                 "      Sampling temperature.\n"
                 "  --repeat-penalty 1.2\n"
                 "      Penalty for repeated tokens.\n"
                 "  --n-predict 1000\n"
                 "      Maximum audio tokens.\n"
                 "  --trim-fade-samples 480\n"
                 "      Samples faded at the end of the wav.\n"
                 "  --top-p 0.95 for nano and turbo, 1.0 for v3\n"
                 "      Nucleus sampling cutoff.\n"
                 "  --cfm-steps 2 for nano and turbo, 5 for v3\n"
                 "      Flow matching steps. More steps are slower.\n"
                 "  --top-k 1000\n"
                 "      Nano and turbo only. Keep this many tokens.\n"
                 "  --min-p 0.05\n"
                 "      v3 only. Minimum token probability.\n"
                 "  --cfg-weight 0.5\n"
                 "      v3 only. Classifier-free guidance weight.\n"
                 "  --exaggeration 0.5\n"
                 "      v3 only. How far the delivery moves from the reference.\n"
                 "  --cfm-cfg 0.7\n"
                 "      v3 only. Guidance inside the flow matcher.\n"
                 "Passing a v3-only flag to nano or turbo is an error.\n",
                 argv0, argv0, argv0, argv0, argv0);
}

bool knob(const std::string& name) {
    return name == "--seed" || name == "--temperature" || name == "--top-k" || name == "--top-p" ||
           name == "--repeat-penalty" || name == "--n-predict" || name == "--cfm-steps" ||
           name == "--trim-fade-samples" || name == "--min-p" || name == "--cfg-weight" ||
           name == "--exaggeration" || name == "--cfm-cfg";
}
} // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        usage(argv[0]);
        return 2;
    }

    std::string text;
    std::string language = "en";
    std::string out_path;
    int gpu = 0;
    bool persist = false;
    bool unload = false;
    std::vector<std::pair<std::string, std::string>> overrides;

    if (std::string(argv[1]) == "--unload") return trident::unload_named("chatterbox");
    const std::string a1 = argv[1];
    const bool by_variant = (a1 == "nano" || a1 == "turbo" || a1 == "v3");
    int opt = by_variant ? 2 : 4;
    if (!by_variant && argc < 5) {
        usage(argv[0]);
        return 2;
    }

    for (int i = opt; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--persist")
            persist = true;
        else if (a == "--unload")
            unload = true;
        else if (a == "-t" && i + 1 < argc)
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
    if (unload) return trident::unload_named("chatterbox");
    if (text.empty() && !persist) {
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
        if (persist) {
            const auto values = trident::load_trident();
            const auto request = trident::cfg_path(values, "chatterbox.prompt-file");
            const auto response = trident::cfg_path(values, "chatterbox.response-file");
            trident::write_pid("chatterbox");
            std::filesystem::remove(trident::slot("chatterbox", "stop"));
            auto seen = std::filesystem::file_time_type::min();
            while (!std::filesystem::exists(trident::slot("chatterbox", "stop"))) {
                if (std::filesystem::is_regular_file(request)) {
                    const auto stamp = std::filesystem::last_write_time(request);
                    if (stamp != seen) {
                        seen = stamp;
                        std::ifstream in(request);
                        std::stringstream buffer;
                        buffer << in.rdbuf();
                        const auto line = buffer.str();
                        if (!line.empty()) {
                            auto spoken = engine->synthesize(line, language);
                            auto wav = response;
                            wav.replace_extension(".wav");
                            trident::chatterbox_write_wav(wav, spoken, 24000);
                            std::ofstream(response, std::ios::trunc) << wav.string();
                        }
                    }
                }
                Sleep(100);
            }
            std::filesystem::remove(trident::slot("chatterbox", "pid"));
            std::filesystem::remove(trident::slot("chatterbox", "stop"));
            return 0;
        }
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
