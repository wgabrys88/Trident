#include "common/chatterbox_runtime.h"
#include "common/config.h"
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <mmsystem.h>
#include <string>
#pragma comment(lib, "winmm.lib")

namespace {

trident::Knobs knobs_from(const std::map<std::string, std::string>& values) {
    trident::Knobs knobs;
    knobs.gpu = trident::cfg_int(values, "chatterbox.gpu");
    knobs.seed = trident::cfg_int(values, "chatterbox.seed");
    knobs.temperature = trident::cfg_float(values, "chatterbox.temperature");
    knobs.repeat_penalty = trident::cfg_float(values, "chatterbox.repeat-penalty");
    knobs.n_predict = trident::cfg_int(values, "chatterbox.n-predict");
    knobs.trim_fade = trident::cfg_int(values, "chatterbox.trim-fade-samples");
    knobs.graph_nodes = trident::cfg_int(values, "chatterbox.graph-nodes");
    knobs.end_trim = trident::cfg_int(values, "chatterbox.end-trim-samples");
    knobs.top_p = trident::cfg_float(values, "chatterbox.top-p");
    knobs.cfm_steps = trident::cfg_int(values, "chatterbox.cfm-steps");
    knobs.top_k = trident::cfg_int(values, "chatterbox.top-k");
    knobs.min_p = trident::cfg_float(values, "chatterbox.min-p");
    knobs.cfg_weight = trident::cfg_float(values, "chatterbox.cfg-weight");
    knobs.exaggeration = trident::cfg_float(values, "chatterbox.exaggeration");
    knobs.cfm_cfg = trident::cfg_float(values, "chatterbox.cfm-cfg");
    return knobs;
}

} // namespace

int main(int argc, char** argv) {
    trident::no_args(argc, argv);
    const auto values = trident::load_trident();
    if (trident::cfg_on(values, "chatterbox.unload")) return trident::unload_named("chatterbox");
    if (trident::resident("chatterbox")) return 0;
    const auto variant = trident::need(values, "chatterbox.variant");
    const auto language = trident::need(values, "chatterbox.language");
    const int rate = trident::cfg_int(values, "chatterbox.sample-rate");
    const int poll_ms = trident::cfg_int(values, "chatterbox.poll-ms");
    const auto request = trident::cfg_path(values, "chatterbox.prompt-file");
    const auto response = trident::cfg_path(values, "chatterbox.response-file");
    const auto t3 = trident::cfg_path(values, variant + ".t3");
    const auto s3 = trident::cfg_path(values, variant + ".s3");
    auto engine = trident::chatterbox_make_engine(t3, s3, knobs_from(values));
    auto speak = [&](const std::string& text) {
        auto wav = response;
        wav.replace_extension(".wav");
        trident::chatterbox_write_wav(wav, engine->synthesize(text, language), rate);
        std::ofstream(response, std::ios::binary | std::ios::trunc) << trident::path_u8(wav);
        PlaySoundW(wav.wstring().c_str(), nullptr, SND_FILENAME);
    };
    if (trident::cfg_on(values, "chatterbox.persist"))
        return trident::watch("chatterbox", request, poll_ms, speak);
    const auto text = trident::read_text(request);
    if (text.empty()) {
        std::fprintf(stderr, "chatterbox.prompt-file is empty\n");
        return 2;
    }
    speak(text);
    return 0;
}
