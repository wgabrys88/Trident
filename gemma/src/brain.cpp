#define NOMINMAX
#define WIN32_LEAN_AND_MEAN

#include "common.h"
#include "ggml-cpu.h"
#if defined(GEMMA_CUDA)
#include "ggml-cuda.h"
#else
#include "ggml-vulkan.h"
#endif
#include "mtmd-helper.h"
#include "mtmd.h"
#include "sampling.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <initializer_list>
#include <sstream>
#include <string>
#include <utility>
#include <thread>
#include <vector>
#include <windows.h>
#include "common/config.h"

namespace {

static int host_thread_count() {
    const int hw = (int)std::thread::hardware_concurrency();
    if (hw <= 0) die("--threads is required when the host thread count is unknown");
    return hw;
}

static void require_gpu(int device) {
#if defined(GEMMA_CUDA)
    const int count = ggml_backend_cuda_get_device_count();
    if (count <= 0) die("no CUDA device");
    if (device < 0 || device >= count) die_fmt("CUDA device %d is outside 0..%d", device, count - 1);
    char name[256];
    size_t free_b = 0, total_b = 0;
    ggml_backend_cuda_get_device_description(device, name, sizeof(name));
    ggml_backend_cuda_get_device_memory(device, &free_b, &total_b);
    std::fprintf(stderr, "cuda[%d] %s | free %zu MiB / total %zu MiB\n", device, name, free_b / (1024 * 1024),
                 total_b / (1024 * 1024));
#else
    const int count = ggml_backend_vk_get_device_count();
    if (count <= 0) die("no Vulkan device");
    if (device < 0 || device >= count) die_fmt("Vulkan device %d is outside 0..%d", device, count - 1);
    char name[256];
    size_t free_b = 0, total_b = 0;
    ggml_backend_vk_get_device_description(device, name, sizeof(name));
    ggml_backend_vk_get_device_memory(device, &free_b, &total_b);
    std::fprintf(stderr, "vulkan[%d] %s | free %zu MiB / total %zu MiB\n", device, name, free_b / (1024 * 1024),
                 total_b / (1024 * 1024));
#endif
}

template <class T>
static T pick(const char * value, std::initializer_list<std::pair<const char *, T>> items, const char * name) {
    for (const auto & item : items)
        if (!std::strcmp(value, item.first)) return item.second;
    die_fmt("%s value %s is not valid", name, value);
}

static ggml_type cache_type(const char * value) {
    return pick<ggml_type>(value, {{"q8_0", GGML_TYPE_Q8_0}, {"q4_0", GGML_TYPE_Q4_0}, {"f16", GGML_TYPE_F16}, {"bf16", GGML_TYPE_BF16}, {"f32", GGML_TYPE_F32}}, "cache type");
}

static bool on_off(const char * value, const char * name) {
    return pick<bool>(value, {{"on", true}, {"1", true}, {"off", false}, {"0", false}}, name);
}

static void cpu_mask(common_cpu_params & cpu, const char * text) {
    std::memset(cpu.cpumask, 0, sizeof(cpu.cpumask));
    cpu.mask_valid = true;
    for (int i = 0; text[i]; ++i) {
        if (i >= GGML_MAX_N_THREADS) die("cpu mask is longer than GGML_MAX_N_THREADS");
        if (text[i] == '1') cpu.cpumask[i] = true;
        else if (text[i] != '0') die_fmt("cpu mask %s is 0 and 1", text);
    }
}

static void tensor_split(common_params & params, const char * text) {
    std::stringstream stream(text);
    std::string part;
    int i = 0;
    while (std::getline(stream, part, ',')) {
        if (i >= 128) die("tensor split has more than 128 devices");
        params.tensor_split[i++] = (float)std::atof(part.c_str());
    }
}

static const char * need(int & i, int argc, char ** argv, const char * name) {
    if (i + 1 >= argc) die_fmt("%s needs a value", name);
    return argv[++i];
}

static std::filesystem::path gemma_file(const char * name) {
    wchar_t buf[MAX_PATH];
    const DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    const auto exe = std::filesystem::path(buf, buf + n).parent_path();
    return std::filesystem::weakly_canonical(exe / ".." / ".." / "models" / name);
}

static void usage() {
    const auto model = gemma_file("gemma-4-E2B-it-Q4_0.gguf");
    const auto mmproj = gemma_file("mmproj-gemma-4-E2B-it-Q8_0.gguf");
    std::fprintf(stderr,
                 "usage: gemma-brain [knobs] <prompt> [image]\n"
                 "       gemma-brain --persist\n"
                 "       gemma-brain --unload\n"
                 "--persist loads the model once and reads gemma.prompt-file from trident.txt.\n"
                 "--unload stops that process. A later prompt is handed to it and does not reload.\n"
                 "example: gemma-brain \"Say hello in one sentence.\"\n"
                 "example: gemma-brain \"Explain this image.\" screenshot.png\n"
                 "The answer is stdout. Logs are stderr. The prompt must not start with a dash.\n"
                 "Weights are resolved from this executable: gemma/build/Release -> gemma/models.\n"
                 "  --model %s\n"
                 "      Text GGUF. Default is gemma/models/gemma-4-E2B-it-Q4_0.gguf beside this build.\n"
                 "  --mmproj %s\n"
                 "      Vision projector. Used only when an image path is given.\n"
                 "  --mmproj-gpu on\n"
                 "      Run the projector on the GPU. on or off.\n"
                 "  --mmproj-timings on\n"
                 "      Print projector timings. on or off.\n"
                 "  --cache-type-k q8_0\n"
                 "      Key cache type: q8_0 q4_0 f16 bf16 f32.\n"
                 "  --cache-type-v q8_0\n"
                 "      Value cache type: q8_0 q4_0 f16 bf16 f32.\n"
                 "  --ctx 2048\n"
                 "      Context length in tokens, including the prompt and the image.\n"
                 "  --batch 512\n"
                 "      Logical batch size while processing the prompt.\n"
                 "  --ubatch 512\n"
                 "      Physical batch. For an image, set this at least as large as the image token count.\n"
                 "  --n-predict 512\n"
                 "      Maximum new tokens. The reply stops earlier on end of generation.\n"
                 "  --n-keep 0\n"
                 "      Tokens kept when the context shifts. 0 keeps none.\n"
                 "  --n-chunks -1\n"
                 "      How many context chunks to process. -1 means all.\n"
                 "  --n-parallel 1\n"
                 "      Parallel sequences in one batch.\n"
                 "  --n-sequences 1\n"
                 "      Sequences requested from one prompt.\n"
                 "  --n-outputs-max 0\n"
                 "      Cap on total outputs. 0 means no extra cap.\n"
                 "  --n-outputs-max-per-seq 1\n"
                 "      Cap on outputs from one sequence.\n"
                 "  --grp-attn-n 1\n"
                 "      Grouped-query attention group size.\n"
                 "  --grp-attn-w 512\n"
                 "      Grouped attention width.\n"
                 "  --n-print -1\n"
                 "      How often to print progress. -1 keeps the built-in rate.\n"
                 "  --gpu-layers 999\n"
                 "      Layers to put on the GPU. 999 means all of them.\n"
                 "  --gpu 0\n"
                 "      GPU index. 0 is the first Vulkan or CUDA device.\n"
                 "  --tensor-split\n"
                 "      Comma-separated weights across GPUs. Empty uses one GPU.\n"
                 "  --split none\n"
                 "      Multi-GPU split: none layer row tensor.\n"
                 "  --fit off\n"
                 "      Shrink the context until it fits. on or off.\n"
                 "  --fit-print off\n"
                 "      Print the fit decision. on or off.\n"
                 "  --fit-min-ctx 4096\n"
                 "      Smallest context --fit may choose.\n"
                 "  --load-mode auto\n"
                 "      Weight load: auto none mmap mlock mmap-mlock direct-io.\n"
                 "  --lazy-mode auto\n"
                 "      Lazy tensor load: off auto on.\n"
                 "  --numa disabled\n"
                 "      NUMA policy: disabled distribute isolate numactl mirror.\n"
                 "  --flash-attn on\n"
                 "      Flash attention: auto on off.\n"
                 "  --warmup on\n"
                 "      Run one warmup pass before the prompt. on or off.\n"
                 "  --verbosity 2\n"
                 "      Log level. Higher prints more.\n"
                 "  --threads <host>\n"
                 "      CPU threads for generation. Default is the logical processor count.\n"
                 "  --threads-batch <host>\n"
                 "      CPU threads while processing the prompt. Same default as --threads.\n"
                 "  --priority high\n"
                 "      Generation thread priority: low normal medium high realtime.\n"
                 "  --priority-batch high\n"
                 "      Prompt thread priority: low normal medium high realtime.\n"
                 "  --poll 50\n"
                 "      Generation poll interval.\n"
                 "  --poll-batch 50\n"
                 "      Prompt poll interval.\n"
                 "  --strict-cpu off\n"
                 "      Pin generation to --cpu-mask. on or off.\n"
                 "  --strict-cpu-batch off\n"
                 "      Pin the prompt pass to --cpu-mask-batch. on or off.\n"
                 "  --cpu-mask\n"
                 "      Bit string of allowed generation CPUs, for example 11110000. Empty uses every CPU.\n"
                 "  --cpu-mask-batch\n"
                 "      Bit string of allowed prompt CPUs. Empty uses every CPU.\n"
                 "  --rope-scaling unspecified\n"
                 "      RoPE scaling: unspecified none linear yarn longrope.\n"
                 "  --rope-freq-base 0\n"
                 "      RoPE frequency base. 0 keeps the GGUF value.\n"
                 "  --rope-freq-scale 0\n"
                 "      RoPE frequency scale. 0 keeps the GGUF value.\n"
                 "  --yarn-ext-factor -1\n"
                 "      YaRN extrapolation factor. -1 keeps the model default.\n"
                 "  --yarn-attn-factor -1\n"
                 "      YaRN attention factor. -1 keeps the model default.\n"
                 "  --yarn-beta-fast -1\n"
                 "      YaRN beta fast. -1 keeps the model default.\n"
                 "  --yarn-beta-slow -1\n"
                 "      YaRN beta slow. -1 keeps the model default.\n"
                 "  --yarn-orig-ctx 0\n"
                 "      Original YaRN context. 0 keeps the model default.\n"
                 "  --ctx-shift off\n"
                 "      Shift the context when it fills. on or off.\n"
                 "  --swa-full off\n"
                 "      Full sliding-window attention. on or off.\n"
                 "  --kv-unified off\n"
                 "      One KV buffer for every sequence. on or off.\n"
                 "  --no-kv-offload off\n"
                 "      Keep the KV cache off the GPU when on.\n"
                 "  --check-tensors off\n"
                 "      Check tensor shapes while loading. on or off.\n"
                 "  --no-op-offload off\n"
                 "      Skip offloading no-op nodes when on.\n"
                 "  --no-extra-bufts off\n"
                 "      Skip extra backend buffers when on.\n"
                 "  --no-host off\n"
                 "      Skip the host buffer type when on.\n"
                 "  --show-timings on\n"
                 "      Print context timings at the end. on or off.\n"
                 "  --no-perf off\n"
                 "      Hide performance numbers when on.\n"
                 "  --sampler-no-perf off\n"
                 "      Hide sampler performance numbers when on.\n"
                 "  --image-min-tokens -1\n"
                 "      Minimum vision tokens. -1 uses the GGUF budget. Set min and max together.\n"
                 "  --image-max-tokens -1\n"
                 "      Maximum vision tokens. -1 uses the GGUF budget. This file's soft budgets are 70 140 280 560 1120.\n"
                 "  --mtmd-batch 1024\n"
                 "      Projector batch size in tokens.\n"
                 "  --temp 0.8\n"
                 "      Sampling temperature. Lower is more repetitive.\n"
                 "  --top-k 40\n"
                 "      Keep the top k tokens. 0 disables the cap.\n"
                 "  --top-p 0.95\n"
                 "      Nucleus sampling cutoff.\n"
                 "  --min-p 0.05\n"
                 "      Minimum token probability.\n"
                 "  --repeat-penalty 1.0\n"
                 "      Penalty for tokens that already appeared. 1.0 disables it.\n"
                 "  --seed -1\n"
                 "      Sampler seed. -1 is LLAMA_DEFAULT_SEED.\n"
                 "  --n-prev 64\n"
                 "      Tokens looked at by the repetition penalty.\n"
                 "  --n-probs 0\n"
                 "      How many token probabilities to print. 0 prints none.\n"
                 "  --min-keep 0\n"
                 "      Minimum tokens kept after sampling filters.\n"
                 "  --xtc-probability 0\n"
                 "      Probability of excluding the top choice. 0 disables it.\n"
                 "  --xtc-threshold 0.1\n"
                 "      XTC probability threshold.\n"
                 "  --typical 1\n"
                 "      Typical sampling. 1 disables it.\n"
                 "  --dynatemp-range 0\n"
                 "      Dynamic temperature range. 0 disables it.\n"
                 "  --dynatemp-exponent 1\n"
                 "      Dynamic temperature curve.\n"
                 "  --repeat-last-n 64\n"
                 "      How many recent tokens the repeat penalty sees.\n"
                 "  --frequency-penalty 0\n"
                 "      Penalty scaled by how often a token appeared.\n"
                 "  --presence-penalty 0\n"
                 "      Penalty for any token that appeared at least once.\n"
                 "  --dry-multiplier 0\n"
                 "      DRY repetition penalty strength. 0 disables it.\n"
                 "  --dry-base 1.75\n"
                 "      DRY penalty base.\n"
                 "  --dry-allowed-length 2\n"
                 "      DRY allowed repeat length before the penalty grows.\n"
                 "  --dry-penalty-last-n 64\n"
                 "      Recent tokens considered by DRY.\n"
                 "  --adaptive-target -1\n"
                 "      Adaptive-p target entropy. -1 disables it.\n"
                 "  --adaptive-decay 0.9\n"
                 "      Adaptive-p decay.\n"
                 "  --mirostat 0\n"
                 "      Mirostat mode. 0 off, 1 or 2 on.\n"
                 "  --top-n-sigma -1\n"
                 "      Top-n-sigma cutoff. -1 disables it.\n"
                 "  --mirostat-tau 5\n"
                 "      Mirostat target surprise.\n"
                 "  --mirostat-eta 0.1\n"
                 "      Mirostat learning rate.\n"
                 "  --ignore-eos off\n"
                 "      Keep generating through the end token. on or off.\n"
                 "  --timing-per-token off\n"
                 "      Print a timing line for every token. on or off.\n"
                 "  --backend-sampling off\n"
                 "      Sample on the backend. on or off.\n"
                 "Build flags that need a rebuild are install.py knobs, not these.\n",
                 model.string().c_str(), mmproj.string().c_str());
}

static common_params default_params() {
    common_params p;
    p.model.path = gemma_file("gemma-4-E2B-it-Q4_0.gguf").string();
    p.mmproj.path = gemma_file("mmproj-gemma-4-E2B-it-Q8_0.gguf").string();
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
    p.image_min_tokens = -1;
    p.image_max_tokens = -1;
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

    void open_mmproj(const common_params & params, bool print_timings) {
        mtmd_context_params mp = mtmd_context_params_default();
        mp.use_gpu = params.mmproj_use_gpu;
        mp.print_timings = print_timings;
        mp.n_threads = params.cpuparams.n_threads;
        mp.flash_attn_type = params.flash_attn_type;
        mp.warmup = params.warmup;
        mp.image_min_tokens = params.image_min_tokens;
        mp.image_max_tokens = params.image_max_tokens;
        mp.batch_max_tokens = params.mtmd_batch_max_tokens;
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

    void reset() {
        llama_memory_clear(llama_get_memory(lctx), true);
        n_past = 0;
        common_sampler_reset(smpl);
    }

    std::string generate(int max_tokens) {
        std::string out;
        for (int i = 0; i < max_tokens; ++i) {
            const llama_token id = common_sampler_sample(smpl, lctx, -1);
            common_sampler_accept(smpl, id, true);
            if (llama_vocab_is_eog(vocab, id)) break;
            const auto piece = common_token_to_piece(lctx, id);
            out += piece;
            std::fputs(piece.c_str(), stdout);
            std::fflush(stdout);
            common_batch_clear(batch);
            common_batch_add(batch, id, n_past++, {0}, true);
            if (llama_decode(lctx, batch) != 0) die("llama_decode failed");
        }
        std::fputc('\n', stdout);
        return out;
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
    common_params params = default_params();
    bool mmproj_timings = true;
    bool persist = false;
    bool unload = false;
    std::string prompt;
    std::string image;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "-h" || a == "--help") {
            usage();
            return 0;
        } else if (a == "--persist")
            persist = true;
        else if (a == "--unload")
            unload = true;
        else if (a == "--model")
            params.model.path = need(i, argc, argv, "--model");
        else if (a == "--mmproj")
            params.mmproj.path = need(i, argc, argv, "--mmproj");
        else if (a == "--mmproj-gpu")
            params.mmproj_use_gpu = on_off(need(i, argc, argv, "--mmproj-gpu"), "--mmproj-gpu");
        else if (a == "--mmproj-timings")
            mmproj_timings = on_off(need(i, argc, argv, "--mmproj-timings"), "--mmproj-timings");
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
        else if (a == "--n-keep")
            params.n_keep = std::atoi(need(i, argc, argv, "--n-keep"));
        else if (a == "--n-chunks")
            params.n_chunks = std::atoi(need(i, argc, argv, "--n-chunks"));
        else if (a == "--n-parallel")
            params.n_parallel = std::atoi(need(i, argc, argv, "--n-parallel"));
        else if (a == "--n-sequences")
            params.n_sequences = std::atoi(need(i, argc, argv, "--n-sequences"));
        else if (a == "--n-outputs-max")
            params.n_outputs_max = std::atoi(need(i, argc, argv, "--n-outputs-max"));
        else if (a == "--n-outputs-max-per-seq")
            params.n_outputs_max_per_seq = std::atoi(need(i, argc, argv, "--n-outputs-max-per-seq"));
        else if (a == "--grp-attn-n")
            params.grp_attn_n = std::atoi(need(i, argc, argv, "--grp-attn-n"));
        else if (a == "--grp-attn-w")
            params.grp_attn_w = std::atoi(need(i, argc, argv, "--grp-attn-w"));
        else if (a == "--n-print")
            params.n_print = std::atoi(need(i, argc, argv, "--n-print"));
        else if (a == "--gpu-layers")
            params.n_gpu_layers = std::atoi(need(i, argc, argv, "--gpu-layers"));
        else if (a == "--gpu")
            params.main_gpu = std::atoi(need(i, argc, argv, "--gpu"));
        else if (a == "--tensor-split")
            tensor_split(params, need(i, argc, argv, "--tensor-split"));
        else if (a == "--split")
            params.split_mode = pick<llama_split_mode>(need(i, argc, argv, "--split"), {{"none", LLAMA_SPLIT_MODE_NONE}, {"layer", LLAMA_SPLIT_MODE_LAYER}, {"row", LLAMA_SPLIT_MODE_ROW}, {"tensor", LLAMA_SPLIT_MODE_TENSOR}}, "--split");
        else if (a == "--load-mode")
            params.load_mode = pick<llama_load_mode>(need(i, argc, argv, "--load-mode"), {{"auto", LLAMA_LOAD_MODE_AUTO}, {"none", LLAMA_LOAD_MODE_NONE}, {"mmap", LLAMA_LOAD_MODE_MMAP}, {"mlock", LLAMA_LOAD_MODE_MLOCK}, {"mmap-mlock", LLAMA_LOAD_MODE_MMAP_MLOCK}, {"direct-io", LLAMA_LOAD_MODE_DIRECT_IO}}, "--load-mode");
        else if (a == "--lazy-mode")
            params.lazy_mode = pick<llama_lazy_mode>(need(i, argc, argv, "--lazy-mode"), {{"off", LLAMA_LAZY_MODE_OFF}, {"auto", LLAMA_LAZY_MODE_AUTO}, {"on", LLAMA_LAZY_MODE_ON}}, "--lazy-mode");
        else if (a == "--numa")
            params.numa = pick<ggml_numa_strategy>(need(i, argc, argv, "--numa"), {{"disabled", GGML_NUMA_STRATEGY_DISABLED}, {"distribute", GGML_NUMA_STRATEGY_DISTRIBUTE}, {"isolate", GGML_NUMA_STRATEGY_ISOLATE}, {"numactl", GGML_NUMA_STRATEGY_NUMACTL}, {"mirror", GGML_NUMA_STRATEGY_MIRROR}}, "--numa");
        else if (a == "--threads")
            params.cpuparams.n_threads = std::atoi(need(i, argc, argv, "--threads"));
        else if (a == "--threads-batch")
            params.cpuparams_batch.n_threads = std::atoi(need(i, argc, argv, "--threads-batch"));
        else if (a == "--priority")
            params.cpuparams.priority = pick<ggml_sched_priority>(need(i, argc, argv, "--priority"), {{"low", GGML_SCHED_PRIO_LOW}, {"normal", GGML_SCHED_PRIO_NORMAL}, {"medium", GGML_SCHED_PRIO_MEDIUM}, {"high", GGML_SCHED_PRIO_HIGH}, {"realtime", GGML_SCHED_PRIO_REALTIME}}, "--priority");
        else if (a == "--priority-batch")
            params.cpuparams_batch.priority = pick<ggml_sched_priority>(need(i, argc, argv, "--priority-batch"), {{"low", GGML_SCHED_PRIO_LOW}, {"normal", GGML_SCHED_PRIO_NORMAL}, {"medium", GGML_SCHED_PRIO_MEDIUM}, {"high", GGML_SCHED_PRIO_HIGH}, {"realtime", GGML_SCHED_PRIO_REALTIME}}, "--priority-batch");
        else if (a == "--poll")
            params.cpuparams.poll = (uint32_t)std::atoi(need(i, argc, argv, "--poll"));
        else if (a == "--poll-batch")
            params.cpuparams_batch.poll = (uint32_t)std::atoi(need(i, argc, argv, "--poll-batch"));
        else if (a == "--strict-cpu")
            params.cpuparams.strict_cpu = on_off(need(i, argc, argv, "--strict-cpu"), "--strict-cpu");
        else if (a == "--strict-cpu-batch")
            params.cpuparams_batch.strict_cpu = on_off(need(i, argc, argv, "--strict-cpu-batch"), "--strict-cpu-batch");
        else if (a == "--cpu-mask")
            cpu_mask(params.cpuparams, need(i, argc, argv, "--cpu-mask"));
        else if (a == "--cpu-mask-batch")
            cpu_mask(params.cpuparams_batch, need(i, argc, argv, "--cpu-mask-batch"));
        else if (a == "--fit")
            params.fit_params = on_off(need(i, argc, argv, "--fit"), "--fit");
        else if (a == "--fit-print")
            params.fit_params_print = on_off(need(i, argc, argv, "--fit-print"), "--fit-print");
        else if (a == "--fit-min-ctx")
            params.fit_params_min_ctx = std::atoi(need(i, argc, argv, "--fit-min-ctx"));
        else if (a == "--flash-attn")
            params.flash_attn_type = pick<llama_flash_attn_type>(need(i, argc, argv, "--flash-attn"), {{"auto", LLAMA_FLASH_ATTN_TYPE_AUTO}, {"on", LLAMA_FLASH_ATTN_TYPE_ENABLED}, {"1", LLAMA_FLASH_ATTN_TYPE_ENABLED}, {"off", LLAMA_FLASH_ATTN_TYPE_DISABLED}, {"0", LLAMA_FLASH_ATTN_TYPE_DISABLED}}, "--flash-attn");
        else if (a == "--warmup")
            params.warmup = on_off(need(i, argc, argv, "--warmup"), "--warmup");
        else if (a == "--verbosity")
            params.verbosity = std::atoi(need(i, argc, argv, "--verbosity"));
        else if (a == "--rope-scaling")
            params.rope_scaling_type = pick<llama_rope_scaling_type>(need(i, argc, argv, "--rope-scaling"), {{"unspecified", LLAMA_ROPE_SCALING_TYPE_UNSPECIFIED}, {"none", LLAMA_ROPE_SCALING_TYPE_NONE}, {"linear", LLAMA_ROPE_SCALING_TYPE_LINEAR}, {"yarn", LLAMA_ROPE_SCALING_TYPE_YARN}, {"longrope", LLAMA_ROPE_SCALING_TYPE_LONGROPE}}, "--rope-scaling");
        else if (a == "--rope-freq-base")
            params.rope_freq_base = (float)std::atof(need(i, argc, argv, "--rope-freq-base"));
        else if (a == "--rope-freq-scale")
            params.rope_freq_scale = (float)std::atof(need(i, argc, argv, "--rope-freq-scale"));
        else if (a == "--yarn-ext-factor")
            params.yarn_ext_factor = (float)std::atof(need(i, argc, argv, "--yarn-ext-factor"));
        else if (a == "--yarn-attn-factor")
            params.yarn_attn_factor = (float)std::atof(need(i, argc, argv, "--yarn-attn-factor"));
        else if (a == "--yarn-beta-fast")
            params.yarn_beta_fast = (float)std::atof(need(i, argc, argv, "--yarn-beta-fast"));
        else if (a == "--yarn-beta-slow")
            params.yarn_beta_slow = (float)std::atof(need(i, argc, argv, "--yarn-beta-slow"));
        else if (a == "--yarn-orig-ctx")
            params.yarn_orig_ctx = std::atoi(need(i, argc, argv, "--yarn-orig-ctx"));
        else if (a == "--ctx-shift")
            params.ctx_shift = on_off(need(i, argc, argv, "--ctx-shift"), "--ctx-shift");
        else if (a == "--swa-full")
            params.swa_full = on_off(need(i, argc, argv, "--swa-full"), "--swa-full");
        else if (a == "--kv-unified")
            params.kv_unified = on_off(need(i, argc, argv, "--kv-unified"), "--kv-unified");
        else if (a == "--no-kv-offload")
            params.no_kv_offload = on_off(need(i, argc, argv, "--no-kv-offload"), "--no-kv-offload");
        else if (a == "--check-tensors")
            params.check_tensors = on_off(need(i, argc, argv, "--check-tensors"), "--check-tensors");
        else if (a == "--no-op-offload")
            params.no_op_offload = on_off(need(i, argc, argv, "--no-op-offload"), "--no-op-offload");
        else if (a == "--no-extra-bufts")
            params.no_extra_bufts = on_off(need(i, argc, argv, "--no-extra-bufts"), "--no-extra-bufts");
        else if (a == "--no-host")
            params.no_host = on_off(need(i, argc, argv, "--no-host"), "--no-host");
        else if (a == "--show-timings")
            params.show_timings = on_off(need(i, argc, argv, "--show-timings"), "--show-timings");
        else if (a == "--no-perf")
            params.no_perf = on_off(need(i, argc, argv, "--no-perf"), "--no-perf");
        else if (a == "--sampler-no-perf")
            params.sampling.no_perf = on_off(need(i, argc, argv, "--sampler-no-perf"), "--sampler-no-perf");
        else if (a == "--image-min-tokens")
            params.image_min_tokens = std::atoi(need(i, argc, argv, "--image-min-tokens"));
        else if (a == "--image-max-tokens")
            params.image_max_tokens = std::atoi(need(i, argc, argv, "--image-max-tokens"));
        else if (a == "--mtmd-batch")
            params.mtmd_batch_max_tokens = std::atoi(need(i, argc, argv, "--mtmd-batch"));
        else if (a == "--temp")
            params.sampling.temp = (float)std::atof(need(i, argc, argv, "--temp"));
        else if (a == "--top-k")
            params.sampling.top_k = std::atoi(need(i, argc, argv, "--top-k"));
        else if (a == "--top-p")
            params.sampling.top_p = (float)std::atof(need(i, argc, argv, "--top-p"));
        else if (a == "--min-p")
            params.sampling.min_p = (float)std::atof(need(i, argc, argv, "--min-p"));
        else if (a == "--repeat-penalty")
            params.sampling.penalty_repeat = (float)std::atof(need(i, argc, argv, "--repeat-penalty"));
        else if (a == "--seed")
            params.sampling.seed = (uint32_t)std::strtoul(need(i, argc, argv, "--seed"), nullptr, 10);
        else if (a == "--n-prev")
            params.sampling.n_prev = std::atoi(need(i, argc, argv, "--n-prev"));
        else if (a == "--n-probs")
            params.sampling.n_probs = std::atoi(need(i, argc, argv, "--n-probs"));
        else if (a == "--min-keep")
            params.sampling.min_keep = std::atoi(need(i, argc, argv, "--min-keep"));
        else if (a == "--xtc-probability")
            params.sampling.xtc_probability = (float)std::atof(need(i, argc, argv, "--xtc-probability"));
        else if (a == "--xtc-threshold")
            params.sampling.xtc_threshold = (float)std::atof(need(i, argc, argv, "--xtc-threshold"));
        else if (a == "--typical")
            params.sampling.typ_p = (float)std::atof(need(i, argc, argv, "--typical"));
        else if (a == "--dynatemp-range")
            params.sampling.dynatemp_range = (float)std::atof(need(i, argc, argv, "--dynatemp-range"));
        else if (a == "--dynatemp-exponent")
            params.sampling.dynatemp_exponent = (float)std::atof(need(i, argc, argv, "--dynatemp-exponent"));
        else if (a == "--repeat-last-n")
            params.sampling.penalty_last_n = std::atoi(need(i, argc, argv, "--repeat-last-n"));
        else if (a == "--frequency-penalty")
            params.sampling.penalty_freq = (float)std::atof(need(i, argc, argv, "--frequency-penalty"));
        else if (a == "--presence-penalty")
            params.sampling.penalty_present = (float)std::atof(need(i, argc, argv, "--presence-penalty"));
        else if (a == "--dry-multiplier")
            params.sampling.dry_multiplier = (float)std::atof(need(i, argc, argv, "--dry-multiplier"));
        else if (a == "--dry-base")
            params.sampling.dry_base = (float)std::atof(need(i, argc, argv, "--dry-base"));
        else if (a == "--dry-allowed-length")
            params.sampling.dry_allowed_length = std::atoi(need(i, argc, argv, "--dry-allowed-length"));
        else if (a == "--dry-penalty-last-n")
            params.sampling.dry_penalty_last_n = std::atoi(need(i, argc, argv, "--dry-penalty-last-n"));
        else if (a == "--adaptive-target")
            params.sampling.adaptive_target = (float)std::atof(need(i, argc, argv, "--adaptive-target"));
        else if (a == "--adaptive-decay")
            params.sampling.adaptive_decay = (float)std::atof(need(i, argc, argv, "--adaptive-decay"));
        else if (a == "--mirostat")
            params.sampling.mirostat = std::atoi(need(i, argc, argv, "--mirostat"));
        else if (a == "--top-n-sigma")
            params.sampling.top_n_sigma = (float)std::atof(need(i, argc, argv, "--top-n-sigma"));
        else if (a == "--mirostat-tau")
            params.sampling.mirostat_tau = (float)std::atof(need(i, argc, argv, "--mirostat-tau"));
        else if (a == "--mirostat-eta")
            params.sampling.mirostat_eta = (float)std::atof(need(i, argc, argv, "--mirostat-eta"));
        else if (a == "--ignore-eos")
            params.sampling.ignore_eos = on_off(need(i, argc, argv, "--ignore-eos"), "--ignore-eos");
        else if (a == "--timing-per-token")
            params.sampling.timing_per_token = on_off(need(i, argc, argv, "--timing-per-token"), "--timing-per-token");
        else if (a == "--backend-sampling")
            params.sampling.backend_sampling = on_off(need(i, argc, argv, "--backend-sampling"), "--backend-sampling");
        else if (a[0] == '-')
            die_fmt("unknown argument: %s", a.c_str());
        else if (prompt.empty())
            prompt = a;
        else if (image.empty())
            image = a;
        else
            die("usage: gemma-brain [knobs] <prompt> [image]");
    }
    if (unload) return trident::unload_named("gemma");
    if (prompt.empty() && !persist) {
        usage();
        return 2;
    }
    if (!persist && !prompt.empty() && trident::resident("gemma")) {
        const auto values = trident::load_trident();
        const auto request = trident::cfg_path(values, "gemma.prompt-file");
        const auto response = trident::cfg_path(values, "gemma.response-file");
        const auto before = std::filesystem::exists(response) ? std::filesystem::last_write_time(response) : std::filesystem::file_time_type::min();
        std::ofstream(request, std::ios::trunc) << prompt;
        for (int i = 0; i < 6000; ++i) {
            if (std::filesystem::exists(response) && std::filesystem::last_write_time(response) != before) {
                std::ifstream in(response);
                std::stringstream buffer;
                buffer << in.rdbuf();
                std::fputs(buffer.str().c_str(), stdout);
                std::fputc('\n', stdout);
                return 0;
            }
            Sleep(50);
        }
        die("resident gemma did not answer");
    }
    ggml_backend_load_all();
    require_gpu(params.main_gpu);
    Gemma gemma(params);
    if (persist) {
        const auto values = trident::load_trident();
        const auto request = trident::cfg_path(values, "gemma.prompt-file");
        const auto response = trident::cfg_path(values, "gemma.response-file");
        trident::write_pid("gemma");
        std::filesystem::remove(trident::slot("gemma", "stop"));
        auto seen = std::filesystem::file_time_type::min();
        while (!std::filesystem::exists(trident::slot("gemma", "stop"))) {
            if (std::filesystem::is_regular_file(request)) {
                const auto stamp = std::filesystem::last_write_time(request);
                if (stamp != seen) {
                    seen = stamp;
                    std::ifstream in(request);
                    std::stringstream buffer;
                    buffer << in.rdbuf();
                    const auto text = buffer.str();
                    if (!text.empty()) {
                        gemma.reset();
                        gemma.eval_text(text);
                        const auto answer = gemma.generate(params.n_predict);
                        std::ofstream(response, std::ios::trunc) << answer;
                    }
                }
            }
            Sleep(100);
        }
        std::filesystem::remove(trident::slot("gemma", "pid"));
        std::filesystem::remove(trident::slot("gemma", "stop"));
        return 0;
    }
    if (!image.empty()) {
        gemma.open_mmproj(params, mmproj_timings);
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
