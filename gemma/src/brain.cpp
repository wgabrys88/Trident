// Gemma 4 E2B on GTX 1060 (sm_61): multimodal text + image + audio, CUDA-only ggml build.
// Requires -m and --mmproj. No CPU tensor backend, no fit_params shrink, no text-only path.

#define NOMINMAX
#define WIN32_LEAN_AND_MEAN

#include "chat.h"
#include "common.h"
#include "ggml-cuda.h"
#include "mtmd-helper.h"
#include "mtmd.h"
#include "sampling.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#ifndef GEMMA_HOST_THREADS
#define GEMMA_HOST_THREADS 0
#endif

namespace {

struct Config {
    std::string model  = "models/gemma-4-E2B-it-Q4_0.gguf";
    std::string mmproj = "models/mmproj-gemma-4-E2B-it-BF16.gguf";
    std::string prompt;
    std::vector<std::string> media;
    int n_ctx     = 2048;
    int n_predict = 512;
};

void usage(const char * argv0) {
    std::fprintf(stderr,
                 "usage: %s -m <model.gguf> --mmproj <mmproj.gguf> [options]\n"
                 "  --image <path>  --audio <path>  (repeat; attach to next user turn or -p)\n"
                 "  -p <prompt>     single turn then exit\n"
                 "  -c <n_ctx>       default 2048 (6GB multimodal)\n"
                 "  -n <tokens>      max generation tokens (default 512)\n"
                 "  Env: GEMMA_MODEL, GEMMA_MMPROJ\n",
                 argv0);
}

Config parse_args(int argc, char ** argv) {
    Config cfg;
    if (const char * env = std::getenv("GEMMA_MODEL")) cfg.model = env;
    if (const char * env = std::getenv("GEMMA_MMPROJ")) cfg.mmproj = env;

    for (int i = 1; i < argc; ++i) {
        const char * a = argv[i];
        if (!std::strcmp(a, "-m") && i + 1 < argc) {
            cfg.model = argv[++i];
        } else if (!std::strcmp(a, "--mmproj") && i + 1 < argc) {
            cfg.mmproj = argv[++i];
        } else if (!std::strcmp(a, "--image") && i + 1 < argc) {
            cfg.media.push_back(argv[++i]);
        } else if (!std::strcmp(a, "--audio") && i + 1 < argc) {
            cfg.media.push_back(argv[++i]);
        } else if (!std::strcmp(a, "-p") && i + 1 < argc) {
            cfg.prompt = argv[++i];
        } else if (!std::strcmp(a, "-c") && i + 1 < argc) {
            cfg.n_ctx = std::atoi(argv[++i]);
        } else if (!std::strcmp(a, "-n") && i + 1 < argc) {
            cfg.n_predict = std::atoi(argv[++i]);
        } else if (!std::strcmp(a, "-h") || !std::strcmp(a, "--help")) {
            usage(argv[0]);
            std::exit(0);
        } else {
            die_fmt("unknown argument: %s", a);
        }
    }
    if (cfg.model.empty() || cfg.mmproj.empty()) {
        usage(argv[0]);
        die("both -m and --mmproj are required");
    }
    if (cfg.n_ctx < 512) die("n_ctx too small");
    if (cfg.n_predict < 1) die("n_predict must be >= 1");
    return cfg;
}

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

static common_params params_gtx1060(const Config & cfg) {
    common_params p;
    p.model.path   = cfg.model;
    p.mmproj.path  = cfg.mmproj;
    p.mmproj_use_gpu = true;
    p.mmproj_device  = nullptr;
    p.no_mmproj      = false;
    p.use_jinja      = true;

    p.n_ctx    = cfg.n_ctx;
    p.n_batch  = 512;
    p.n_ubatch = 512;
    p.n_predict = cfg.n_predict;

    p.n_gpu_layers = 999;
    p.main_gpu     = 0;
    p.split_mode   = LLAMA_SPLIT_MODE_NONE;
    p.fit_params   = false;

    p.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
    p.warmup          = true;

    const int nth = host_thread_count();
    p.cpuparams.n_threads       = nth;
    p.cpuparams_batch.n_threads = nth;
    p.cpuparams.priority        = GGML_SCHED_PRIO_HIGH;
    p.cpuparams_batch.priority  = GGML_SCHED_PRIO_HIGH;

    p.sampling.temp  = 0.8f;
    p.sampling.top_p = 0.95f;
    p.sampling.min_p = 0.05f;

    p.verbosity = 2;
    return p;
}

struct Gemma {
    common_init_result_ptr llama;
    llama_model *          model = nullptr;
    llama_context *        lctx  = nullptr;
    const llama_vocab *    vocab = nullptr;
    common_sampler *       smpl  = nullptr;
    llama_batch            batch{};
    int                    n_batch = 512;
    llama_pos              n_past  = 0;

    mtmd::context_ptr           mtmd_ctx;
    mtmd::bitmaps               pending_media;
    mtmd::batch_ptr             mbatch;
    mtmd_helper_init_opt        media_opt = mtmd_helper_init_opt_default();

    common_chat_templates_ptr   tmpls;
    std::vector<common_chat_msg> history;
    bool use_jinja = true;

    explicit Gemma(common_params & params)
        : llama(common_init_from_params(params)), n_batch(params.n_batch), use_jinja(params.use_jinja) {
        model = llama->model();
        lctx  = llama->context();
        if (!model || !lctx) die("model or context load failed");
        if (!llama_supports_gpu_offload()) die("build lacks GPU offload");

        vocab = llama_model_get_vocab(model);
        smpl  = common_sampler_init(model, params.sampling);
        batch = llama_batch_init(1, 0, 1);

        mtmd_context_params mp = mtmd_context_params_default();
        mp.use_gpu         = true;
        mp.device          = nullptr;
        mp.print_timings   = true;
        mp.n_threads       = host_thread_count();
        mp.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
        mp.warmup          = true;

        mtmd_ctx.reset(mtmd_init_from_file(params.mmproj.path.c_str(), model, mp));
        if (!mtmd_ctx) die_fmt("mmproj load failed: %s", params.mmproj.path.c_str());
        if (!mtmd_helper_model_can_chat(lctx, mtmd_ctx.get())) die("model/mmproj cannot chat");
        if (!llama_model_chat_template(model, nullptr)) die("model has no chat template");

        tmpls = common_chat_templates_init(model, "");
    }

    ~Gemma() {
        llama_batch_free(batch);
        common_sampler_free(smpl);
    }

    void load_media(const char * path) {
        auto res = mtmd_helper_bitmap_init_from_file(mtmd_ctx.get(), path, false, media_opt);
        if (!res.bitmap) die_fmt("media load failed: %s", path);
        pending_media.entries.emplace_back(res.bitmap);
    }

    std::string format_user_message(const common_chat_msg & msg) {
        return common_chat_format_single(tmpls.get(), history, msg, msg.role == "user", use_jinja);
    }

    void ingest_user_turn(const std::string & text) {
        common_chat_msg msg;
        msg.role    = "user";
        msg.content = text;
        run_eval(msg);
    }

    void run_eval(const common_chat_msg & msg) {
        const bool add_bos = history.empty();
        const std::string formatted = format_user_message(msg);
        history.push_back(msg);

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
        if (segments.size() - 1 != bitmaps.size()) {
            die_fmt("media markers (%zu) != loaded media (%zu)", segments.size() - 1, bitmaps.size());
        }

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
                                                     (int32_t)part_ptrs.size(), add_bos);
        if (tok != 0) die_fmt("mtmd_tokenize_from_parts failed (%d)", tok);

        pending_media.entries.clear();

        const size_t n_chunks = mtmd_input_chunks_size(chunks.ptr.get());
        for (size_t i = 0; i < n_chunks; ++i) {
            const mtmd_input_chunk * chunk = mtmd_input_chunks_get(chunks.ptr.get(), i);
            const auto chunk_type    = mtmd_input_chunk_get_type(chunk);

            if (chunk_type == MTMD_INPUT_CHUNK_TYPE_TEXT) {
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
            const int32_t res =
                mtmd_helper_decode_image_chunk(mtmd_ctx.get(), lctx, chunk, embd, n_past, 0, n_batch, &new_n_past, nullptr, nullptr);
            if (res != 0) die_fmt("media chunk decode failed at %zu (%d)", i, res);
            n_past = new_n_past;
        }
    }

    void generate(int max_tokens) {
        std::fputs("assistant: ", stdout);
        std::fflush(stdout);

        llama_tokens generated;
        for (int i = 0; i < max_tokens; ++i) {
            const llama_token id = common_sampler_sample(smpl, lctx, -1);
            common_sampler_accept(smpl, id, true);
            generated.push_back(id);

            if (llama_vocab_is_eog(vocab, id)) {
                std::fputc('\n', stdout);
                break;
            }

            std::fputs(common_token_to_piece(lctx, id).c_str(), stdout);
            std::fflush(stdout);

            common_batch_clear(batch);
            common_batch_add(batch, id, n_past++, {0}, true);
            if (llama_decode(lctx, batch) != 0) die("llama_decode failed");
        }

        common_chat_msg reply;
        reply.role    = "assistant";
        reply.content = common_detokenize(lctx, generated);
        history.push_back(std::move(reply));
        std::fputc('\n', stdout);
    }

    void clear_chat() {
        n_past = 0;
        history.clear();
        pending_media.entries.clear();
        llama_memory_clear(llama_get_memory(lctx), true);
    }
};

} // namespace

int main(int argc, char ** argv) {
    ggml_time_init();
    common_init();

    const Config cfg = parse_args(argc, argv);
    common_params params = params_gtx1060(cfg);

    ggml_backend_load_all();
    require_cuda_gpu0();

    Gemma gemma(params);
    std::fprintf(stderr, "gemma-brain | cuda sm_61 | host_threads=%d | n_ctx=%d | %s | %s\n", host_thread_count(),
                 cfg.n_ctx, cfg.model.c_str(), cfg.mmproj.c_str());

    for (const auto & path : cfg.media) gemma.load_media(path.c_str());

    if (!cfg.prompt.empty()) {
        std::string user = cfg.prompt;
        if (user.find(mtmd_default_marker()) == std::string::npos) {
            for (size_t i = 0; i < cfg.media.size(); ++i) user = mtmd_default_marker() + user;
        }
        gemma.ingest_user_turn(user);
        gemma.generate(cfg.n_predict);
    } else {
        Gemma * g = &gemma;
        const int n_predict = cfg.n_predict;
        std::fprintf(stderr, "commands: /image <path>  /audio <path>  /clear  /quit\n");
        std::string pending_text;
        for (;;) {
            std::fputs("user> ", stdout);
            std::fflush(stdout);
            std::string line;
            if (!std::getline(std::cin, line)) break;
            if (line.empty()) continue;
            if (line == "/quit" || line == "/exit") break;
            if (line == "/clear") {
                g->clear_chat();
                pending_text.clear();
                continue;
            }
            if (line.rfind("/image ", 0) == 0 || line.rfind("/audio ", 0) == 0) {
                g->load_media(line.substr(7).c_str());
                pending_text += mtmd_default_marker();
                continue;
            }
            pending_text += line;
            g->ingest_user_turn(pending_text);
            g->generate(n_predict);
            pending_text.clear();
        }
    }

    llama_perf_context_print(gemma.lctx);
    return 0;
}
