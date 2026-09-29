#include "common.h"
#include "sampling.h"
#include "common/config.h"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>

namespace {

struct Gate {
    common_init_result_ptr llama;
    llama_context* lctx = nullptr;
    const llama_vocab* vocab = nullptr;
    common_sampler* smpl = nullptr;
    llama_batch batch{};
    int n_batch = 512;
    llama_pos n_past = 0;
    int n_predict = 128;

    explicit Gate(common_params& params)
        : llama(common_init_from_params(params)), n_batch(params.n_batch), n_predict(params.n_predict) {
        lctx = llama->context();
        if (!llama->model() || !lctx) trident::fail("sense model load failed");
        vocab = llama_model_get_vocab(llama->model());
        smpl = common_sampler_init(llama->model(), params.sampling);
        batch = llama_batch_init(n_batch, 0, 1);
    }
    ~Gate() {
        llama_batch_free(batch);
        common_sampler_free(smpl);
    }
    std::string generate() {
        std::string out;
        for (int i = 0; i < n_predict; ++i) {
            const llama_token id = common_sampler_sample(smpl, lctx, -1);
            common_sampler_accept(smpl, id, true);
            if (llama_vocab_is_eog(vocab, id)) break;
            const auto piece = common_token_to_piece(lctx, id);
            out += piece;
            common_batch_clear(batch);
            common_batch_add(batch, id, n_past++, {0}, true);
            if (llama_decode(lctx, batch) != 0) throw std::runtime_error("sense decode failed");
        }
        return out;
    }
    std::string answer(const std::string& text) {
        llama_memory_clear(llama_get_memory(lctx), true);
        n_past = 0;
        common_sampler_reset(smpl);
        const auto tokens = common_tokenize(lctx, text, false, true);
        if (tokens.empty()) throw std::runtime_error("sense tokenize failed");
        for (size_t i = 0; i < tokens.size();) {
            const size_t n = std::min(size_t(n_batch), tokens.size() - i);
            common_batch_clear(batch);
            for (size_t j = 0; j < n; ++j)
                common_batch_add(batch, tokens[i + j], n_past++, {0}, i + j + 1 == tokens.size());
            if (llama_decode(lctx, batch) != 0) throw std::runtime_error("sense prefill failed");
            i += n;
        }
        return generate();
    }
};

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
    publish(dir / "sense.response.txt", id + "\n" + line + "\n");
}

void reply_ok(const std::filesystem::path& dir, const std::string& id, const std::string& text) {
    publish(dir / "sense.response.txt", id + "\nok\n" + text);
}

std::string trim_cr(std::string text) {
    if (!text.empty() && text.back() == '\r') text.pop_back();
    return text;
}

bool readable_id(const std::string& id) {
    return !id.empty() && id.size() <= 64;
}

std::string first_line(const std::filesystem::path& path, bool& got_nl) {
    got_nl = false;
    std::ifstream in(path, std::ios::binary);
    if (!in) return {};
    std::string line;
    char c;
    while (in.get(c)) {
        if (c == '\n') {
            got_nl = true;
            break;
        }
        if (line.size() >= 64) break;
        line.push_back(c);
    }
    return trim_cr(line);
}

bool hex64(const std::string& text) {
    if (text.size() != 64) return false;
    for (unsigned char c : text) {
        if (c >= '0' && c <= '9') continue;
        if (c >= 'a' && c <= 'f') continue;
        return false;
    }
    return true;
}

std::string loaded_fingerprint(const std::filesystem::path& path, unsigned long pid) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return {};
    std::string lines[3];
    int count = 0;
    std::string line;
    while (count < 3 && std::getline(in, line)) lines[count++] = trim_cr(line);
    if (count < 2) return {};
    char* end = nullptr;
    const unsigned long have = std::strtoul(lines[0].c_str(), &end, 10);
    if (end == lines[0].c_str() || *end || have != pid) return {};
    if (!hex64(lines[1])) return {};
    return lines[1];
}

void replace_pid_file(const std::filesystem::path& path, const std::string& body) {
    const auto tmp = path.parent_path() / "sense.pid.tmp";
    trident::write_named(tmp, body);
    if (!MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        std::error_code ec;
        std::filesystem::remove(tmp, ec);
        trident::fail("cannot publish " + trident::path_u8(path));
    }
}

void hold_ready(unsigned long pid, std::string& loaded) {
    std::ifstream in(resident_pid_path, std::ios::binary);
    if (!in) return;
    std::string lines[3];
    int count = 0;
    std::string line;
    while (count < 3 && std::getline(in, line)) lines[count++] = trim_cr(line);
    in.close();
    if (count < 1) return;
    char* end = nullptr;
    const unsigned long have = std::strtoul(lines[0].c_str(), &end, 10);
    if (end == lines[0].c_str() || *end || have != pid) return;
    if (count < 2 || !hex64(lines[1])) return;
    if (loaded.empty()) loaded = lines[1];
    else if (lines[1] != loaded) return;
    if (count >= 3 && lines[2] == "ready") return;
    replace_pid_file(resident_pid_path, std::to_string(pid) + "\n" + loaded + "\nready\n");
}

void handle_prompt(const std::filesystem::path& dir, Gate& gate) {
    const auto path = dir / "sense.prompt.txt";
    if (!is_file(path)) return;
    std::error_code ec;
    const auto size = std::filesystem::file_size(path, ec);
    if (ec) return;
    if (size > 1000000) {
        bool nl = false;
        const auto id = first_line(path, nl);
        std::filesystem::remove(path, ec);
        std::fprintf(stderr, "resident error prompt too long\n");
        std::fflush(stderr);
        if (nl && decimal_id(id)) reply(dir, id, "err prompt too long");
        else if (readable_id(id)) reply(dir, id, "err prompt too long");
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
        const auto id = trim_cr(body);
        std::fprintf(stderr, "resident error bad prompt\n");
        std::fflush(stderr);
        if (readable_id(id)) reply(dir, id, "err bad prompt");
        return;
    }
    auto id = trim_cr(body.substr(0, split));
    auto text = body.substr(split + 1);
    if (!decimal_id(id)) {
        std::fprintf(stderr, "resident error bad prompt id\n");
        std::fflush(stderr);
        if (readable_id(id)) reply(dir, id, "err bad prompt id");
        else reply(dir, "0", "err bad prompt id");
        return;
    }
    if (text.empty()) {
        reply(dir, id, "err empty");
        return;
    }
    try {
        const auto t0 = std::chrono::steady_clock::now();
        const auto generated = gate.answer(text);
        const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
        std::fprintf(stderr, "resident generate %lld ms\n", static_cast<long long>(ms));
        std::fflush(stderr);
        reply_ok(dir, id, generated);
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

int serve(Gate& gate) {
    const auto dir = std::filesystem::current_path();
    const auto pid = GetCurrentProcessId();
    resident_pid_path = dir / "sense.pid";
    std::atexit(remove_resident_pid);
    if (is_file(dir / "sense.stop")) {
        std::error_code ec;
        std::filesystem::remove(dir / "sense.stop", ec);
        std::fprintf(stderr, "resident stop before ready\n");
        std::fflush(stderr);
        return 0;
    }
    std::string loaded = loaded_fingerprint(resident_pid_path, pid);
    std::string body = std::to_string(pid) + "\n";
    if (!loaded.empty()) body += loaded + "\nready\n";
    replace_pid_file(resident_pid_path, body);
    std::fprintf(stderr, "resident ready pid %lu\n", static_cast<unsigned long>(pid));
    std::fflush(stderr);
    for (;;) {
        hold_ready(pid, loaded);
        handle_prompt(dir, gate);
        if (is_file(dir / "sense.stop")) {
            std::error_code ec;
            std::filesystem::remove(dir / "sense.stop", ec);
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
        trident::fail("usage: sense.exe file.txt | sense.exe --resident file.txt");
    }
    if (resident) std::setvbuf(stderr, nullptr, _IONBF, 0);
    char* args[] = {argv[0], const_cast<char*>(file)};
    const auto values = trident::load_settings(2, args);
    common_init();
    common_params params;
    params.model.path = trident::path_u8(trident::cfg_path(values, "sense.model"));
    params.n_ctx = trident::cfg_int(values, "sense.ctx");
    params.n_predict = trident::cfg_int(values, "sense.n-predict");
    params.n_batch = trident::cfg_int(values, "sense.batch");
    params.cpuparams.n_threads = trident::cfg_int(values, "sense.threads");
    params.cpuparams_batch.n_threads = params.cpuparams.n_threads;
    params.n_gpu_layers = trident::cfg_int(values, "sense.gpu-layers");
    params.sampling.temp = trident::cfg_float(values, "sense.temp");
    params.sampling.top_k = trident::cfg_int(values, "sense.top-k");
    params.sampling.top_p = trident::cfg_float(values, "sense.top-p");
    ggml_backend_load_all();
    const auto load_t0 = std::chrono::steady_clock::now();
    Gate gate(params);
    if (!resident) {
        try {
            trident::write_output("sense", gate.answer(trident::cfg_key(values, "sense.text")));
        } catch (const std::exception& ex) {
            trident::fail(ex.what());
        }
        return 0;
    }
    const auto load_ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - load_t0).count();
    std::fprintf(stderr, "resident load %lld ms\n", static_cast<long long>(load_ms));
    std::fflush(stderr);
    return serve(gate);
}
