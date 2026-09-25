#include "common.h"
#include "sampling.h"
#include "common/config.h"
#include <algorithm>
#include <cctype>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace {

static std::string trim_copy(std::string text) {
    auto sp = [](unsigned char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; };
    while (!text.empty() && sp((unsigned char)text.front())) text.erase(text.begin());
    while (!text.empty() && sp((unsigned char)text.back())) text.pop_back();
    return text;
}

static bool tool_pass(const std::string& text) {
    std::string current;
    auto consider = [&](std::string line) {
        while (!line.empty() && std::isspace((unsigned char)line.front())) line.erase(line.begin());
        while (!line.empty() && std::isspace((unsigned char)line.back())) line.pop_back();
        for (char& c : line) c = (char)std::tolower((unsigned char)c);
        while (!line.empty() && (line.back() == '.' || line.back() == ',' || line.back() == '!')) line.pop_back();
        return line == "tool pass";
    };
    for (char c : text) {
        if (c == '\n') {
            if (consider(current)) return true;
            current.clear();
        } else current.push_back(c);
    }
    return consider(current);
}

struct Gate {
    common_init_result_ptr llama;
    llama_context* lctx = nullptr;
    const llama_vocab* vocab = nullptr;
    common_sampler* smpl = nullptr;
    llama_batch batch{};
    int n_batch = 512;
    llama_pos n_past = 0;
    int n_predict = 128;
    std::filesystem::path memory_path;
    int memory_max = 1000;
    std::vector<std::string> room;
    std::string system;

    Gate(common_params& params, const std::string& instructions, const std::filesystem::path& memory, int limit)
        : llama(common_init_from_params(params)), n_batch(params.n_batch), n_predict(params.n_predict), memory_path(memory), memory_max(limit), system(instructions) {
        lctx = llama->context();
        if (!llama->model() || !lctx) trident::fail("sense model load failed");
        vocab = llama_model_get_vocab(llama->model());
        smpl = common_sampler_init(llama->model(), params.sampling);
        batch = llama_batch_init(n_batch, 0, 1);
        if (std::filesystem::is_regular_file(memory_path)) {
            std::ifstream in(memory_path, std::ios::binary);
            std::stringstream buffer;
            buffer << in.rdbuf();
            std::string raw = buffer.str(), part;
            for (char c : raw) {
                if (c == '\n' && !part.empty() && part.back() == '\n') {
                    part.pop_back();
                    part = trim_copy(part);
                    if (!part.empty()) room.push_back(part);
                    part.clear();
                } else part.push_back(c);
            }
            part = trim_copy(part);
            if (!part.empty()) room.push_back(part);
            compact();
        }
    }
    ~Gate() {
        llama_batch_free(batch);
        common_sampler_free(smpl);
    }
    void compact() {
        auto total = [&]() {
            int n = 0;
            for (const auto& part : room) n += (int)part.size() + 2;
            return n;
        };
        while (room.size() > 1 && total() > memory_max) room.erase(room.begin());
    }
    void save() {
        compact();
        std::ofstream out(memory_path, std::ios::binary | std::ios::trunc);
        for (size_t i = 0; i < room.size(); ++i) {
            if (i) out << "\n\n";
            out << room[i];
        }
        if (!room.empty()) out << "\n";
    }
    std::string generate() {
        std::string out;
        const std::string stop = "<|im_end|>";
        for (int i = 0; i < n_predict; ++i) {
            const llama_token id = common_sampler_sample(smpl, lctx, -1);
            common_sampler_accept(smpl, id, true);
            if (llama_vocab_is_eog(vocab, id)) break;
            const auto piece = common_token_to_piece(lctx, id);
            out += piece;
            if (out.size() >= stop.size() && out.compare(out.size() - stop.size(), stop.size(), stop) == 0) break;
            common_batch_clear(batch);
            common_batch_add(batch, id, n_past++, {0}, true);
            if (llama_decode(lctx, batch) != 0) trident::fail("sense decode failed");
        }
        return out;
    }
    std::string pass_original(const std::string& heard_raw) {
        const std::string heard = trim_copy(heard_raw);
        if (heard.empty()) return {};
        std::string earlier;
        for (size_t i = 0; i < room.size(); ++i) {
            if (i) earlier += "\n";
            earlier += room[i];
        }
        room.push_back(heard);
        save();
        llama_memory_clear(llama_get_memory(lctx), true);
        n_past = 0;
        common_sampler_reset(smpl);
        std::string prompt = "<|im_start|>system\n" + trim_copy(system) + "\n/no_think<|im_end|>\n<|im_start|>user\n";
        if (!earlier.empty()) prompt += earlier + "\n";
        prompt += heard + "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n";
        const auto tokens = common_tokenize(lctx, prompt, true, true);
        for (size_t i = 0; i < tokens.size();) {
            const size_t n = std::min(size_t(n_batch), tokens.size() - i);
            common_batch_clear(batch);
            for (size_t j = 0; j < n; ++j)
                common_batch_add(batch, tokens[i + j], n_past++, {0}, i + j + 1 == tokens.size());
            if (llama_decode(lctx, batch) != 0) trident::fail("sense prefill failed");
            i += n;
        }
        if (tool_pass(generate())) return heard;
        return {};
    }
};

}

int main(int, char**) {
    common_init();
    const auto values = trident::load_trident();
    if (trident::cfg_on(values, "sense.unload")) return trident::unload_named("sense");
    if (trident::resident("sense")) return 0;
    common_params params;
    params.model.path = trident::path_u8(trident::cfg_path(values, "sense.model"));
    params.n_ctx = trident::cfg_int(values, "sense.ctx");
    params.n_predict = trident::cfg_int(values, "sense.n-predict");
    params.n_batch = 512;
    params.cpuparams.n_threads = trident::cfg_int(values, "sense.threads");
    params.n_gpu_layers = trident::cfg_int(values, "sense.gpu-layers");
    params.sampling.temp = trident::cfg_float(values, "sense.temp");
    params.sampling.top_k = trident::cfg_int(values, "sense.top-k");
    params.sampling.top_p = trident::cfg_float(values, "sense.top-p");
    ggml_backend_load_all();
    Gate gate(params, trident::cfg_opt(values, "sense.system"), trident::cfg_path(values, "sense.memory-file"), trident::cfg_int(values, "sense.memory-max"));
    const auto heard = trident::cfg_path(values, "ear.response-file");
    const auto out = trident::cfg_path(values, "gemma.prompt-file");
    const int poll = trident::cfg_int(values, "sense.poll-ms");
    if (!trident::cfg_on(values, "sense.persist")) {
        const auto text = trident::read_text(heard);
        const auto passed = gate.pass_original(text);
        if (!passed.empty()) std::ofstream(out, std::ios::binary | std::ios::trunc) << passed;
        return 0;
    }
    return trident::watch("sense", heard, poll, [&](const std::string& text) {
        const auto passed = gate.pass_original(text);
        if (!passed.empty()) std::ofstream(out, std::ios::binary | std::ios::trunc) << passed;
    });
}
