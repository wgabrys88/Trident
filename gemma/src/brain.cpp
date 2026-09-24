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

static ggml_type cache_type(const char * name) {
    if (!std::strcmp(name, "q8_0")) return GGML_TYPE_Q8_0;
    if (!std::strcmp(name, "q4_0")) return GGML_TYPE_Q4_0;
    if (!std::strcmp(name, "f16")) return GGML_TYPE_F16;
    if (!std::strcmp(name, "bf16")) return GGML_TYPE_BF16;
    if (!std::strcmp(name, "f32")) return GGML_TYPE_F32;
    die_fmt("cache type %s is not q8_0, q4_0, f16, bf16, or f32", name);
}

static bool on_off(const char * value, const char * name) {
    if (!std::strcmp(value, "on") || !std::strcmp(value, "1")) return true;
    if (!std::strcmp(value, "off") || !std::strcmp(value, "0")) return false;
    die_fmt("%s is on or off", name);
}

static const char * need(int & i, int argc, char ** argv, const char * name) {
    if (i + 1 >= argc) die_fmt("%s needs a value", name);
    return argv[++i];
}

static void usage() {
    std::fprintf(stderr,
                 "usage: gemma-brain [knobs] <prompt> [image]\n"
                 "defaults are the current GTX 1060 run:\n"
                 "  --model models/gemma-4-E2B-it-Q4_0.gguf\n"
                 "  --mmproj models/mmproj-gemma-4-E2B-it-Q8_0.gguf --mmproj-gpu on\n"
                 "  --cache-type-k q8_0 --cache-type-v q8_0\n"
                 "  --ctx 2048 --batch 512 --ubatch 512 --n-predict 512\n"
                 "  --gpu-layers 999 --gpu 0 --fit off --flash-attn on --warmup on\n"
                 "  --threads 0 (0 keeps the host count) --image-min-tokens -1 --image-max-tokens -1\n"
                 "  --temp 0.8 --top-k 40 --top-p 0.95 --min-p 0.05 --repeat-penalty 1.0\n"
                 "image tokens use the GGUF budget when min and max stay -1\n");
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

    void open_mmproj(const common_params & params, int image_min, int image_max) {
        mtmd_context_params mp = mtmd_context_params_default();
        mp.use_gpu = params.mmproj_use_gpu;
        mp.print_timings = true;
        mp.n_threads = params.cpuparams.n_threads;
        mp.flash_attn_type = params.flash_attn_type;
        mp.warmup = params.warmup;
        mp.image_min_tokens = image_min;
        mp.image_max_tokens = image_max;
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
    if (argc < 2) {
        usage();
        return 2;
    }
    ggml_time_init();
    common_init();
    common_params params = params_gtx1060();
    int image_min = -1;
    int image_max = -1;
    std::string prompt;
    std::string image;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "-h" || a == "--help") {
            usage();
            return 0;
        } else if (a == "--model")
            params.model.path = need(i, argc, argv, "--model");
        else if (a == "--mmproj")
            params.mmproj.path = need(i, argc, argv, "--mmproj");
        else if (a == "--mmproj-gpu")
            params.mmproj_use_gpu = on_off(need(i, argc, argv, "--mmproj-gpu"), "--mmproj-gpu");
        else if (a == "--cache-type-k")
            params.cache_type_k = cache_type(need(i, argc, argv, "--cache-type-k"));
        else if (a == "--cache-type-v")
            params.cache_type_v = cache_type(need(i, argc, argv, "--cache-type-v"));
        else if (a == "--ctx")
            params.n_ctx = std::atoi(need(i, argc, argv, "--ctx"));
        else if (a == "--batch")
            params.n_batch = std::atoi(need(i, argc, argv, "--batch"));
        else if (a == "--ubatch")
            params.n_ubatch = std::atoi(need(i, argc, argv, "--ubatch"));
        else if (a == "--n-predict")
            params.n_predict = std::atoi(need(i, argc, argv, "--n-predict"));
        else if (a == "--gpu-layers")
            params.n_gpu_layers = std::atoi(need(i, argc, argv, "--gpu-layers"));
        else if (a == "--gpu")
            params.main_gpu = std::atoi(need(i, argc, argv, "--gpu"));
        else if (a == "--threads") {
            const int n = std::atoi(need(i, argc, argv, "--threads"));
            if (n > 0) {
                params.cpuparams.n_threads = n;
                params.cpuparams_batch.n_threads = n;
            }
        } else if (a == "--fit")
            params.fit_params = on_off(need(i, argc, argv, "--fit"), "--fit");
        else if (a == "--flash-attn")
            params.flash_attn_type = on_off(need(i, argc, argv, "--flash-attn"), "--flash-attn")
                                         ? LLAMA_FLASH_ATTN_TYPE_ENABLED
                                         : LLAMA_FLASH_ATTN_TYPE_DISABLED;
        else if (a == "--warmup")
            params.warmup = on_off(need(i, argc, argv, "--warmup"), "--warmup");
        else if (a == "--image-min-tokens")
            image_min = std::atoi(need(i, argc, argv, "--image-min-tokens"));
        else if (a == "--image-max-tokens")
            image_max = std::atoi(need(i, argc, argv, "--image-max-tokens"));
        else if (a == "--temp")
            params.sampling.temp = std::atof(need(i, argc, argv, "--temp"));
        else if (a == "--top-k")
            params.sampling.top_k = std::atoi(need(i, argc, argv, "--top-k"));
        else if (a == "--top-p")
            params.sampling.top_p = std::atof(need(i, argc, argv, "--top-p"));
        else if (a == "--min-p")
            params.sampling.min_p = std::atof(need(i, argc, argv, "--min-p"));
        else if (a == "--repeat-penalty")
            params.sampling.penalty_repeat = std::atof(need(i, argc, argv, "--repeat-penalty"));
        else if (a[0] == '-')
            die_fmt("unknown argument: %s", a.c_str());
        else if (prompt.empty())
            prompt = a;
        else if (image.empty())
            image = a;
        else
            die("usage: gemma-brain [knobs] <prompt> [image]");
    }
    if (prompt.empty()) {
        usage();
        return 2;
    }
    ggml_backend_load_all();
    require_cuda_gpu0();
    Gemma gemma(params);
    if (!image.empty()) {
        gemma.open_mmproj(params, image_min, image_max);
        gemma.load_media(image.c_str());
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
