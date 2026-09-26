#include "common.h"
#include "sampling.h"
#include "common/config.h"
#include <fstream>
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
            if (llama_decode(lctx, batch) != 0) trident::fail("sense decode failed");
        }
        return out;
    }
    std::string answer(const std::string& text) {
        if (text.empty()) return {};
        llama_memory_clear(llama_get_memory(lctx), true);
        n_past = 0;
        common_sampler_reset(smpl);
        const auto tokens = common_tokenize(lctx, text, true, true);
        for (size_t i = 0; i < tokens.size();) {
            const size_t n = std::min(size_t(n_batch), tokens.size() - i);
            common_batch_clear(batch);
            for (size_t j = 0; j < n; ++j)
                common_batch_add(batch, tokens[i + j], n_past++, {0}, i + j + 1 == tokens.size());
            if (llama_decode(lctx, batch) != 0) trident::fail("sense prefill failed");
            i += n;
        }
        return generate();
    }
};

}

int main(int argc, char** argv) {
    trident::no_args(argc, argv);
    common_init();
    const auto values = trident::load_trident();
    if (trident::cfg_on(values, "sense.unload")) return trident::unload_named("sense");
    if (trident::resident("sense")) return 0;
    common_params params;
    params.model.path = trident::path_u8(trident::cfg_path(values, "sense.model"));
    params.n_ctx = trident::cfg_int(values, "sense.ctx");
    params.n_predict = trident::cfg_int(values, "sense.n-predict");
    params.n_batch = trident::cfg_int(values, "sense.batch");
    params.cpuparams.n_threads = trident::cfg_int(values, "sense.threads");
    params.n_gpu_layers = trident::cfg_int(values, "sense.gpu-layers");
    params.sampling.temp = trident::cfg_float(values, "sense.temp");
    params.sampling.top_k = trident::cfg_int(values, "sense.top-k");
    params.sampling.top_p = trident::cfg_float(values, "sense.top-p");
    ggml_backend_load_all();
    Gate gate(params);
    const auto heard = trident::cfg_path(values, "ear.response-file");
    const auto out = trident::cfg_path(values, "gemma.prompt-file");
    const int poll = trident::cfg_int(values, "sense.poll-ms");
    if (!trident::cfg_on(values, "sense.persist")) {
        const auto text = trident::read_text(heard);
        std::ofstream(out, std::ios::binary | std::ios::trunc) << gate.answer(text);
        return 0;
    }
    return trident::watch("sense", heard, poll, [&](const std::string& text) {
        std::ofstream(out, std::ios::binary | std::ios::trunc) << gate.answer(text);
    });
}
