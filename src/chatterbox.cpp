#include "common/chatterbox_runtime.h"
#include "common/config.h"
#include <chrono>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <utility>
#include <mmsystem.h>
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

struct Emitted {
    std::string name;
    bool ok = true;
};

Emitted emit(trident::Synth& engine, const std::string& text, const std::string& language, int rate, bool play) {
    auto pcm = engine.synthesize(text, language);
    auto txt = trident::reserve_output("chatterbox");
    auto wav = txt;
    wav.replace_extension(".wav");
    trident::chatterbox_write_wav(wav, std::move(pcm), rate);
    const auto name = trident::path_u8(wav.filename());
    trident::write_named(txt, name);
    if (play && !PlaySoundW(wav.wstring().c_str(), nullptr, SND_FILENAME)) return {name, false};
    return {name, true};
}

std::filesystem::path resident_pid_path;

void remove_resident_pid() {
    if (resident_pid_path.empty()) return;
    std::error_code ec;
    std::filesystem::remove(resident_pid_path, ec);
    resident_pid_path.clear();
}

bool is_file(const std::filesystem::path& path) {
    std::error_code ec;
    return std::filesystem::is_regular_file(path, ec);
}

void publish(const std::filesystem::path& path, const std::string& body) {
    auto tmp = path.parent_path() / (path.filename().string() + ".tmp");
    trident::write_named(tmp, body);
    std::error_code ec;
    std::filesystem::remove(path, ec);
    std::filesystem::rename(tmp, path, ec);
    if (ec) trident::fail("cannot publish " + trident::path_u8(path));
}

std::string one_line(std::string text) {
    for (char& c : text) {
        if (c == '\n' || c == '\r' || c == '\t') c = ' ';
    }
    if (text.empty()) text = "failed";
    if (text.size() > 400) text.resize(400);
    return text;
}

bool decimal_id(const std::string& id) {
    if (id.empty() || id.size() > 32) return false;
    for (unsigned char c : id) {
        if (c < '0' || c > '9') return false;
    }
    return true;
}

void reply(const std::filesystem::path& dir, const std::string& id, const std::string& line) {
    publish(dir / "mouth.response.txt", id + "\n" + line + "\n");
}

void handle_prompt(const std::filesystem::path& dir, trident::Synth& engine, const std::string& language, int rate, bool play) {
    const auto path = dir / "mouth.prompt.txt";
    if (!is_file(path)) return;
    std::error_code ec;
    const auto size = std::filesystem::file_size(path, ec);
    if (ec) return;
    if (size > 1000000) {
        std::filesystem::remove(path, ec);
        std::fprintf(stderr, "resident error prompt too long\n");
        std::fflush(stderr);
        return;
    }
    std::ifstream in(path, std::ios::binary);
    if (!in) return;
    const auto begin = std::istreambuf_iterator<char>(in);
    const auto end = std::istreambuf_iterator<char>();
    std::string body(begin, end);
    in.close();
    std::filesystem::remove(path, ec);
    if (ec) return;
    const auto split = body.find('\n');
    if (split == std::string::npos) {
        std::fprintf(stderr, "resident error bad prompt\n");
        std::fflush(stderr);
        return;
    }
    auto id = body.substr(0, split);
    if (!id.empty() && id.back() == '\r') id.pop_back();
    auto text = body.substr(split + 1);
    if (!decimal_id(id)) {
        std::fprintf(stderr, "resident error bad prompt id\n");
        std::fflush(stderr);
        return;
    }
    if (text.empty()) {
        reply(dir, id, "err empty");
        return;
    }
    try {
        const auto t0 = std::chrono::steady_clock::now();
        const auto spoken = emit(engine, text, language, rate, play);
        const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
        if (!spoken.ok) {
            std::fprintf(stderr, "resident error playback failed\n");
            std::fflush(stderr);
            reply(dir, id, "err playback failed");
            return;
        }
        std::fprintf(stderr, "resident speak %lld ms\n", static_cast<long long>(ms));
        std::fflush(stderr);
        reply(dir, id, "ok " + spoken.name);
    } catch (const std::exception& ex) {
        const auto msg = one_line(ex.what());
        std::fprintf(stderr, "resident error %s\n", msg.c_str());
        std::fflush(stderr);
        reply(dir, id, "err " + msg);
    } catch (...) {
        std::fprintf(stderr, "resident error failed\n");
        std::fflush(stderr);
        reply(dir, id, "err failed");
    }
}

int serve(trident::Synth& engine, const std::string& variant, const std::string& language, int rate, bool play) {
    if (variant.find_first_of(" \t\r\n") != std::string::npos || language.find_first_of(" \t\r\n") != std::string::npos)
        trident::fail("chatterbox.variant and chatterbox.language must be single tokens");
    const auto dir = std::filesystem::current_path();
    if (is_file(dir / "mouth.stop")) {
        std::error_code ec;
        std::filesystem::remove(dir / "mouth.stop", ec);
        std::fprintf(stderr, "resident stop before ready\n");
        std::fflush(stderr);
        return 0;
    }
    resident_pid_path = dir / "mouth.pid";
    std::atexit(remove_resident_pid);
    trident::write_named(resident_pid_path, std::to_string(GetCurrentProcessId()) + "\n" + variant + "\n" + language + "\n");
    std::fprintf(stderr, "resident ready pid %lu %s %s\n", static_cast<unsigned long>(GetCurrentProcessId()), variant.c_str(), language.c_str());
    std::fflush(stderr);
    for (;;) {
        handle_prompt(dir, engine, language, rate, play);
        if (is_file(dir / "mouth.stop")) {
            std::error_code ec;
            std::filesystem::remove(dir / "mouth.stop", ec);
            std::fprintf(stderr, "resident stop pid %lu\n", static_cast<unsigned long>(GetCurrentProcessId()));
            std::fflush(stderr);
            remove_resident_pid();
            return 0;
        }
        Sleep(20);
    }
}

} // namespace

int main(int argc, char** argv) {
    const char* file = nullptr;
    bool resident = false;
    if (argc == 2 && argv[1] && argv[1][0]) {
        file = argv[1];
    } else if (argc == 3 && argv[1] && std::string(argv[1]) == "--resident" && argv[2] && argv[2][0]) {
        resident = true;
        file = argv[2];
    } else {
        trident::fail("usage: chatterbox.exe file.txt | chatterbox.exe --resident file.txt");
    }
    char* args[] = {argv[0], const_cast<char*>(file)};
    const auto values = trident::load_settings(2, args);
    const auto variant = trident::need(values, "chatterbox.variant");
    const auto language = trident::need(values, "chatterbox.language");
    const int rate = trident::cfg_int(values, "chatterbox.sample-rate");
    const bool play = trident::cfg_on(values, "chatterbox.play");
    const auto t3 = trident::cfg_path(values, variant + ".t3");
    const auto s3 = trident::cfg_path(values, variant + ".s3");
    auto engine = trident::chatterbox_make_engine(t3, s3, knobs_from(values));
    const auto text = trident::cfg_key(values, "chatterbox.text");
    if (!resident) {
        if (!emit(*engine, text, language, rate, play).ok) return 1;
        return 0;
    }
    return serve(*engine, variant, language, rate, play);
}
