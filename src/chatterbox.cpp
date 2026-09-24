#include "common/chatterbox_runtime.h"
#include "common/config.h"
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>
#include <utility>
#include <vector>

int main(int, char**) {
    const auto values = trident::load_trident();
    if (trident::cfg_on(values, "chatterbox.unload")) return trident::unload_named("chatterbox");
    if (trident::resident("chatterbox")) return 0;
    const auto variant = trident::cfg(values, "chatterbox.variant", "turbo");
    const auto language = trident::cfg(values, "chatterbox.language", "en");
    const bool llama = variant == "v3";
    const int gpu = trident::cfg_int(values, "chatterbox.gpu", 0);
    const int rate = trident::cfg_int(values, "chatterbox.sample-rate", 24000);
    const int poll_ms = trident::cfg_int(values, "chatterbox.poll-ms", 100);
    const bool persist = trident::cfg_on(values, "chatterbox.persist");
    std::vector<std::pair<std::string, std::string>> overrides = {
        {"--seed", trident::cfg(values, "chatterbox.seed", "42")},
        {"--temperature", trident::cfg(values, "chatterbox.temperature", "0.8")},
        {"--repeat-penalty", trident::cfg(values, "chatterbox.repeat-penalty", "1.2")},
        {"--n-predict", trident::cfg(values, "chatterbox.n-predict", "1000")},
        {"--trim-fade-samples", trident::cfg(values, "chatterbox.trim-fade-samples", "480")},
        {"--graph-nodes", trident::cfg(values, "chatterbox.graph-nodes", "8192")},
        {"--end-trim-samples", trident::cfg(values, "chatterbox.end-trim-samples", "960")},
        {"--top-p", trident::cfg(values, llama ? "chatterbox.top-p-v3" : "chatterbox.top-p", llama ? "1.0" : "0.95")},
        {"--cfm-steps", trident::cfg(values, llama ? "chatterbox.cfm-steps-v3" : "chatterbox.cfm-steps", llama ? "5" : "2")},
    };
    if (llama) {
        overrides.push_back({"--min-p", trident::cfg(values, "chatterbox.min-p", "0.05")});
        overrides.push_back({"--cfg-weight", trident::cfg(values, "chatterbox.cfg-weight", "0.5")});
        overrides.push_back({"--exaggeration", trident::cfg(values, "chatterbox.exaggeration", "0.5")});
        overrides.push_back({"--cfm-cfg", trident::cfg(values, "chatterbox.cfm-cfg", "0.7")});
    } else {
        overrides.push_back({"--top-k", trident::cfg(values, "chatterbox.top-k", "1000")});
    }
    try {
        auto engine = trident::chatterbox_make_engine(variant, gpu, overrides);
        const auto request = trident::cfg_path(values, "chatterbox.prompt-file");
        const auto response = trident::cfg_path(values, "chatterbox.response-file");
        auto speak = [&](const std::string& text) {
            auto wav = response;
            wav.replace_extension(".wav");
            trident::chatterbox_write_wav(wav, engine->synthesize(text, language), rate);
            std::ofstream(response, std::ios::trunc) << wav.string();
        };
        if (persist) {
            trident::write_pid("chatterbox");
            std::filesystem::remove(trident::slot("chatterbox", "stop"));
            auto seen = std::filesystem::file_time_type::min();
            while (!std::filesystem::exists(trident::slot("chatterbox", "stop"))) {
                if (std::filesystem::is_regular_file(request)) {
                    const auto stamp = std::filesystem::last_write_time(request);
                    if (stamp != seen) {
                        seen = stamp;
                        const auto line = trident::read_text(request);
                        if (!line.empty()) speak(line);
                    }
                }
                Sleep(poll_ms);
            }
            std::filesystem::remove(trident::slot("chatterbox", "pid"));
            std::filesystem::remove(trident::slot("chatterbox", "stop"));
            return 0;
        }
        const auto text = trident::read_text(request);
        if (text.empty()) {
            std::fprintf(stderr, "chatterbox.prompt-file is empty\n");
            return 2;
        }
        auto t0 = std::chrono::steady_clock::now();
        speak(text);
        auto ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
        std::fprintf(stderr, "chatterbox ok | wall %.0f ms\n", ms);
        return 0;
    } catch (const std::exception& err) {
        std::fprintf(stderr, "chatterbox error: %s\n", err.what());
        return 1;
    }
}
