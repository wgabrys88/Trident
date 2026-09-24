#define NOMINMAX
#define WIN32_LEAN_AND_MEAN

#include "common.h"
#include "ggml-cuda.h"
#include "mtmd-helper.h"
#include "mtmd.h"
#include "sampling.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#ifndef GEMMA_HOST_THREADS
#define GEMMA_HOST_THREADS 0
#endif

namespace {

static int host_thread_count() {
    if (const char * env = std::getenv("GEMMA_HOST_THREADS")) {
        const int n = std::atoi(env);
        if (n > 0) return n;
    }
#if GEMMA_HOST_THREADS > 0
    return GEMMA_HOST_THREADS;
#else
    const int hw = (int)std::thread::hardware_concurrency();
    return hw > 0 ? hw : 4;
#endif
}

static void require_cuda_gpu0() {
    if (ggml_backend_cuda_get_device_count() <= 0) die("no CUDA device (GPU-only build)");
    char name[256];
    size_t free_b = 0, total_b = 0;
    ggml_backend_cuda_get_device_description(0, name, sizeof(name));
    ggml_backend_cuda_get_device_memory(0, &free_b, &total_b);
    std::fprintf(stderr, "cuda[0] %s | free %zu MiB / total %zu MiB\n", name, free_b / (1024 * 1024),
                 total_b / (1024 * 1024));
}

static common_params params_gtx1060() {
    common_params p;
    p.model.path = "models/gemma-4-E2B-it-Q4_0.gguf";
    p.mmproj.path = "models/mmproj-gemma-4-E2B-it-Q8_0.gguf";
    p.cache_type_k = GGML_TYPE_Q8_0;
    p.cache_type_v = GGML_TYPE_Q8_0;
    p.mmproj_use_gpu = true;
    p.n_ctx = 2048;
    p.n_batch = 512;
    p.n_ubatch = 512;
    p.n_predict = 512;
    p.n_gpu_layers = 999;
    p.main_gpu = 0;
    p.split_mode = LLAMA_SPLIT_MODE_NONE;
    p.fit_params = false;
    p.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
    p.warmup = true;
    const int nth = host_thread_count();
    p.cpuparams.n_threads = nth;
    p.cpuparams_batch.n_threads = nth;
    p.cpuparams.priority = GGML_SCHED_PRIO_HIGH;
    p.cpuparams_batch.priority = GGML_SCHED_PRIO_HIGH;
    p.verbosity = 2;
    return p;
}

struct Gemma {
    common_init_result_ptr llama;
    llama_model * model = nullptr;
    llama_context * lctx = nullptr;
    const llama_vocab * vocab = nullptr;
    common_sampler * smpl = nullptr;
    llama_batch batch{};
    int n_batch = 512;
    llama_pos n_past = 0;
    mtmd::context_ptr mtmd_ctx;
    mtmd::bitmaps pending_media;
    mtmd::batch_ptr mbatch;
    mtmd_helper_init_opt media_opt = mtmd_helper_init_opt_default();

    explicit Gemma(common_params & params)
        : llama(common_init_from_params(params)), n_batch(params.n_batch) {
        model = llama->model();
        lctx = llama->context();
        if (!model || !lctx) die("model or context load failed");
        if (!llama_supports_gpu_offload()) die("build lacks GPU offload");
        vocab = llama_model_get_vocab(model);
        smpl = common_sampler_init(model, params.sampling);
        batch = llama_batch_init(n_batch, 0, 1);
    }

    ~Gemma() {
        llama_batch_free(batch);
        common_sampler_free(smpl);
    }

    void open_mmproj(const common_params & params) {
        mtmd_context_params mp = mtmd_context_params_default();
        mp.use_gpu = true;
        mp.print_timings = true;
        mp.n_threads = host_thread_count();
        mp.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
        mp.warmup = true;
        mtmd_ctx.reset(mtmd_init_from_file(params.mmproj.path.c_str(), model, mp));
        if (!mtmd_ctx) die_fmt("mmproj load failed: %s", params.mmproj.path.c_str());
    }

    void load_media(const char * path) {
        auto res = mtmd_helper_bitmap_init_from_file(mtmd_ctx.get(), path, false, media_opt);
        if (!res.bitmap) die_fmt("media load failed: %s", path);
        pending_media.entries.emplace_back(res.bitmap);
    }

    void eval_text(const std::string & text) {
        const auto tokens = common_tokenize(lctx, text, true, true);
        for (size_t i = 0; i < tokens.size();) {
            const size_t n = std::min(size_t(n_batch), tokens.size() - i);
            common_batch_clear(batch);
            for (size_t j = 0; j < n; ++j)
                common_batch_add(batch, tokens[i + j], n_past++, {0}, i + j + 1 == tokens.size());
            if (llama_decode(lctx, batch) != 0) die("llama_decode failed");
            i += n;
        }
    }

    void eval_media(const std::string & formatted) {
        const std::string marker = mtmd_default_marker();
        std::vector<std::string> segments;
        size_t start = 0;
        for (;;) {
            const size_t pos = formatted.find(marker, start);
            if (pos == std::string::npos) {
                segments.push_back(formatted.substr(start));
                break;
            }
            segments.push_back(formatted.substr(start, pos - start));
            start = pos + marker.size();
        }
        auto bitmaps = pending_media.c_ptr();
        if (segments.size() - 1 != bitmaps.size())
            die_fmt("media markers (%zu) != loaded media (%zu)", segments.size() - 1, bitmaps.size());
        std::vector<mtmd_input_text> texts(segments.size());
        std::vector<mtmd_input_part> parts;
        for (size_t i = 0; i < segments.size(); ++i) {
            texts[i] = {segments[i].data(), segments[i].size(), false, true};
            parts.push_back({&texts[i], nullptr});
            if (i < bitmaps.size()) parts.push_back({nullptr, bitmaps[i]});
        }
        std::vector<const mtmd_input_part *> part_ptrs;
        for (const auto & part : parts) part_ptrs.push_back(&part);
        mtmd::input_chunks chunks(mtmd_input_chunks_init());
        const int32_t tok = mtmd_tokenize_from_parts(mtmd_ctx.get(), chunks.ptr.get(), part_ptrs.data(),
                                                     (int32_t)part_ptrs.size(), true);
        if (tok != 0) die_fmt("mtmd_tokenize_from_parts failed (%d)", tok);
        pending_media.entries.clear();
        const size_t n_chunks = mtmd_input_chunks_size(chunks.ptr.get());
        for (size_t i = 0; i < n_chunks; ++i) {
            const mtmd_input_chunk * chunk = mtmd_input_chunks_get(chunks.ptr.get(), i);
            if (mtmd_input_chunk_get_type(chunk) == MTMD_INPUT_CHUNK_TYPE_TEXT) {
                llama_pos new_n_past = n_past;
                const int32_t res = mtmd_helper_eval_chunk_single(mtmd_ctx.get(), lctx, chunk, n_past, 0, n_batch,
                                                                  i == n_chunks - 1, &new_n_past);
                if (res != 0) die_fmt("text chunk eval failed at %zu (%d)", i, res);
                n_past = new_n_past;
                continue;
            }
            float * embd = nullptr;
            if (mbatch) embd = mtmd_batch_get_output_embd(mbatch.get(), chunk);
            if (!embd) {
                mbatch.reset(mtmd_batch_init(mtmd_ctx.get()));
                if (mtmd_batch_add_chunk(mbatch.get(), chunk) != 0) die("mtmd_batch_add_chunk");
                for (size_t j = i + 1; j < n_chunks; ++j) {
                    const mtmd_input_chunk * next = mtmd_input_chunks_get(chunks.ptr.get(), j);
                    if (mtmd_input_chunk_get_type(next) == MTMD_INPUT_CHUNK_TYPE_TEXT) break;
                    if (mtmd_batch_add_chunk(mbatch.get(), next) != 0) break;
                }
                if (mtmd_batch_encode(mbatch.get()) != 0) die("mtmd_batch_encode failed");
                embd = mtmd_batch_get_output_embd(mbatch.get(), chunk);
            }
            if (!embd) die("missing mtmd embedding");
            llama_pos new_n_past = n_past;
            const int32_t res = mtmd_helper_decode_image_chunk(mtmd_ctx.get(), lctx, chunk, embd, n_past, 0, n_batch,
                                                              &new_n_past, nullptr, nullptr);
            if (res != 0) die_fmt("media chunk decode failed at %zu (%d)", i, res);
            n_past = new_n_past;
        }
    }

    void generate(int max_tokens) {
        for (int i = 0; i < max_tokens; ++i) {
            const llama_token id = common_sampler_sample(smpl, lctx, -1);
            common_sampler_accept(smpl, id, true);
            if (llama_vocab_is_eog(vocab, id)) break;
            std::fputs(common_token_to_piece(lctx, id).c_str(), stdout);
            std::fflush(stdout);
            common_batch_clear(batch);
            common_batch_add(batch, id, n_past++, {0}, true);
            if (llama_decode(lctx, batch) != 0) die("llama_decode failed");
        }
        std::fputc('\n', stdout);
    }
};

} // namespace

int main(int argc, char ** argv) {
    if (argc < 2 || argc > 3) die("gemma-brain <prompt> [image]");
    ggml_time_init();
    common_init();
    common_params params = params_gtx1060();
    ggml_backend_load_all();
    require_cuda_gpu0();
    Gemma gemma(params);
    std::string prompt = argv[1];
    if (argc == 3) {
        gemma.open_mmproj(params);
        gemma.load_media(argv[2]);
        const std::string marker = mtmd_default_marker();
        if (prompt.find(marker) == std::string::npos) prompt = marker + prompt;
        gemma.eval_media(prompt);
    } else {
        gemma.eval_text(prompt);
    }
    gemma.generate(params.n_predict);
    llama_perf_context_print(gemma.lctx);
    return 0;
}
