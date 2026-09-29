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
#include <chrono>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iterator>
#include <initializer_list>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#include "common/config.h"

namespace {

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
static T pick(const std::string & value, std::initializer_list<std::pair<const char *, T>> items, const char * name) {
    for (const auto & item : items)
        if (value == item.first) return item.second;
    die_fmt("%s value %s is not valid", name, value.c_str());
}

static ggml_type cache_type(const std::string & value) {
    return pick<ggml_type>(value, {{"q8_0", GGML_TYPE_Q8_0}, {"q4_0", GGML_TYPE_Q4_0}, {"f16", GGML_TYPE_F16}, {"bf16", GGML_TYPE_BF16}, {"f32", GGML_TYPE_F32}}, "cache type");
}

static void cpu_mask(common_cpu_params & cpu, const std::string & text) {
    if (text.empty()) return;
    std::memset(cpu.cpumask, 0, sizeof(cpu.cpumask));
    cpu.mask_valid = true;
    for (int i = 0; i < (int)text.size(); ++i) {
        if (i >= GGML_MAX_N_THREADS) die("cpu mask is longer than GGML_MAX_N_THREADS");
        if (text[i] == '1') cpu.cpumask[i] = true;
        else if (text[i] != '0') die_fmt("cpu mask %s is 0 and 1", text.c_str());
    }
}

static void tensor_split(common_params & params, const std::string & text) {
    if (text.empty()) return;
    std::stringstream stream(text);
    std::string part;
    int i = 0;
    while (std::getline(stream, part, ',')) {
        if (i >= 128) die("tensor split has more than 128 devices");
        params.tensor_split[i++] = (float)std::atof(part.c_str());
    }
}

static void apply_config(common_params & params, const std::map<std::string, std::string> & v) {
    params.model.path = trident::path_u8(trident::cfg_path(v, "gemma.model"));
    params.mmproj.path = trident::path_u8(trident::cfg_path(v, "gemma.mmproj"));
    params.mmproj_use_gpu = trident::cfg_on(v, "gemma.mmproj-gpu");
    params.cache_type_k = cache_type(trident::need(v, "gemma.cache-type-k"));
    params.cache_type_v = cache_type(trident::need(v, "gemma.cache-type-v"));
    params.n_ctx = trident::cfg_int(v, "gemma.ctx");
    params.n_batch = trident::cfg_int(v, "gemma.batch");
    params.n_ubatch = trident::cfg_int(v, "gemma.ubatch");
    params.n_predict = trident::cfg_int(v, "gemma.n-predict");
    params.n_keep = trident::cfg_int(v, "gemma.n-keep");
    params.n_chunks = trident::cfg_int(v, "gemma.n-chunks");
    params.n_parallel = trident::cfg_int(v, "gemma.n-parallel");
    params.n_sequences = trident::cfg_int(v, "gemma.n-sequences");
    params.n_outputs_max = trident::cfg_int(v, "gemma.n-outputs-max");
    params.n_outputs_max_per_seq = trident::cfg_int(v, "gemma.n-outputs-max-per-seq");
    params.grp_attn_n = trident::cfg_int(v, "gemma.grp-attn-n");
    params.grp_attn_w = trident::cfg_int(v, "gemma.grp-attn-w");
    params.n_print = trident::cfg_int(v, "gemma.n-print");
    params.n_gpu_layers = trident::cfg_int(v, "gemma.gpu-layers");
    params.main_gpu = trident::cfg_int(v, "gemma.gpu");
    tensor_split(params, trident::cfg_key(v, "gemma.tensor-split"));
    params.split_mode = pick<llama_split_mode>(trident::need(v, "gemma.split"), {{"none", LLAMA_SPLIT_MODE_NONE}, {"layer", LLAMA_SPLIT_MODE_LAYER}, {"row", LLAMA_SPLIT_MODE_ROW}, {"tensor", LLAMA_SPLIT_MODE_TENSOR}}, "gemma.split");
    params.load_mode = pick<llama_load_mode>(trident::need(v, "gemma.load-mode"), {{"auto", LLAMA_LOAD_MODE_AUTO}, {"none", LLAMA_LOAD_MODE_NONE}, {"mmap", LLAMA_LOAD_MODE_MMAP}, {"mlock", LLAMA_LOAD_MODE_MLOCK}, {"mmap-mlock", LLAMA_LOAD_MODE_MMAP_MLOCK}, {"direct-io", LLAMA_LOAD_MODE_DIRECT_IO}}, "gemma.load-mode");
    params.lazy_mode = pick<llama_lazy_mode>(trident::need(v, "gemma.lazy-mode"), {{"off", LLAMA_LAZY_MODE_OFF}, {"auto", LLAMA_LAZY_MODE_AUTO}, {"on", LLAMA_LAZY_MODE_ON}}, "gemma.lazy-mode");
    params.numa = pick<ggml_numa_strategy>(trident::need(v, "gemma.numa"), {{"disabled", GGML_NUMA_STRATEGY_DISABLED}, {"distribute", GGML_NUMA_STRATEGY_DISTRIBUTE}, {"isolate", GGML_NUMA_STRATEGY_ISOLATE}, {"numactl", GGML_NUMA_STRATEGY_NUMACTL}, {"mirror", GGML_NUMA_STRATEGY_MIRROR}}, "gemma.numa");
    params.cpuparams.n_threads = trident::cfg_int(v, "gemma.threads");
    params.cpuparams_batch.n_threads = trident::cfg_int(v, "gemma.threads-batch");
    auto priority = [](const std::string & value, const char * name) {
        return pick<ggml_sched_priority>(value, {{"low", GGML_SCHED_PRIO_LOW}, {"normal", GGML_SCHED_PRIO_NORMAL}, {"medium", GGML_SCHED_PRIO_MEDIUM}, {"high", GGML_SCHED_PRIO_HIGH}, {"realtime", GGML_SCHED_PRIO_REALTIME}}, name);
    };
    params.cpuparams.priority = priority(trident::need(v, "gemma.priority"), "gemma.priority");
    params.cpuparams_batch.priority = priority(trident::need(v, "gemma.priority-batch"), "gemma.priority-batch");
    params.cpuparams.poll = (uint32_t)trident::cfg_int(v, "gemma.poll");
    params.cpuparams_batch.poll = (uint32_t)trident::cfg_int(v, "gemma.poll-batch");
    params.cpuparams.strict_cpu = trident::cfg_on(v, "gemma.strict-cpu");
    params.cpuparams_batch.strict_cpu = trident::cfg_on(v, "gemma.strict-cpu-batch");
    cpu_mask(params.cpuparams, trident::cfg_key(v, "gemma.cpu-mask"));
    cpu_mask(params.cpuparams_batch, trident::cfg_key(v, "gemma.cpu-mask-batch"));
    params.fit_params = trident::cfg_on(v, "gemma.fit");
    params.fit_params_print = trident::cfg_on(v, "gemma.fit-print");
    params.fit_params_min_ctx = trident::cfg_int(v, "gemma.fit-min-ctx");
    params.flash_attn_type = pick<llama_flash_attn_type>(trident::need(v, "gemma.flash-attn"), {{"auto", LLAMA_FLASH_ATTN_TYPE_AUTO}, {"on", LLAMA_FLASH_ATTN_TYPE_ENABLED}, {"off", LLAMA_FLASH_ATTN_TYPE_DISABLED}}, "gemma.flash-attn");
    params.warmup = trident::cfg_on(v, "gemma.warmup");
    params.verbosity = trident::cfg_int(v, "gemma.verbosity");
    params.rope_scaling_type = pick<llama_rope_scaling_type>(trident::need(v, "gemma.rope-scaling"), {{"unspecified", LLAMA_ROPE_SCALING_TYPE_UNSPECIFIED}, {"none", LLAMA_ROPE_SCALING_TYPE_NONE}, {"linear", LLAMA_ROPE_SCALING_TYPE_LINEAR}, {"yarn", LLAMA_ROPE_SCALING_TYPE_YARN}, {"longrope", LLAMA_ROPE_SCALING_TYPE_LONGROPE}}, "gemma.rope-scaling");
    params.rope_freq_base = trident::cfg_float(v, "gemma.rope-freq-base");
    params.rope_freq_scale = trident::cfg_float(v, "gemma.rope-freq-scale");
    params.yarn_ext_factor = trident::cfg_float(v, "gemma.yarn-ext-factor");
    params.yarn_attn_factor = trident::cfg_float(v, "gemma.yarn-attn-factor");
    params.yarn_beta_fast = trident::cfg_float(v, "gemma.yarn-beta-fast");
    params.yarn_beta_slow = trident::cfg_float(v, "gemma.yarn-beta-slow");
    params.yarn_orig_ctx = trident::cfg_int(v, "gemma.yarn-orig-ctx");
    params.ctx_shift = trident::cfg_on(v, "gemma.ctx-shift");
    params.swa_full = trident::cfg_on(v, "gemma.swa-full");
    params.kv_unified = trident::cfg_on(v, "gemma.kv-unified");
    params.no_kv_offload = trident::cfg_on(v, "gemma.no-kv-offload");
    params.check_tensors = trident::cfg_on(v, "gemma.check-tensors");
    params.no_op_offload = trident::cfg_on(v, "gemma.no-op-offload");
    params.no_extra_bufts = trident::cfg_on(v, "gemma.no-extra-bufts");
    params.no_host = trident::cfg_on(v, "gemma.no-host");
    params.show_timings = trident::cfg_on(v, "gemma.show-timings");
    params.no_perf = trident::cfg_on(v, "gemma.no-perf");
    params.sampling.no_perf = trident::cfg_on(v, "gemma.sampler-no-perf");
    params.image_min_tokens = trident::cfg_int(v, "gemma.image-min-tokens");
    params.image_max_tokens = trident::cfg_int(v, "gemma.image-max-tokens");
    params.mtmd_batch_max_tokens = trident::cfg_int(v, "gemma.mtmd-batch");
    params.sampling.temp = trident::cfg_float(v, "gemma.temp");
    params.sampling.top_k = trident::cfg_int(v, "gemma.top-k");
    params.sampling.top_p = trident::cfg_float(v, "gemma.top-p");
    params.sampling.min_p = trident::cfg_float(v, "gemma.min-p");
    params.sampling.penalty_repeat = trident::cfg_float(v, "gemma.repeat-penalty");
    params.sampling.seed = (uint32_t)trident::cfg_int(v, "gemma.seed");
    params.sampling.n_prev = trident::cfg_int(v, "gemma.n-prev");
    params.sampling.n_probs = trident::cfg_int(v, "gemma.n-probs");
    params.sampling.min_keep = trident::cfg_int(v, "gemma.min-keep");
    params.sampling.xtc_probability = trident::cfg_float(v, "gemma.xtc-probability");
    params.sampling.xtc_threshold = trident::cfg_float(v, "gemma.xtc-threshold");
    params.sampling.typ_p = trident::cfg_float(v, "gemma.typical");
    params.sampling.dynatemp_range = trident::cfg_float(v, "gemma.dynatemp-range");
    params.sampling.dynatemp_exponent = trident::cfg_float(v, "gemma.dynatemp-exponent");
    params.sampling.penalty_last_n = trident::cfg_int(v, "gemma.repeat-last-n");
    params.sampling.penalty_freq = trident::cfg_float(v, "gemma.frequency-penalty");
    params.sampling.penalty_present = trident::cfg_float(v, "gemma.presence-penalty");
    params.sampling.dry_multiplier = trident::cfg_float(v, "gemma.dry-multiplier");
    params.sampling.dry_base = trident::cfg_float(v, "gemma.dry-base");
    params.sampling.dry_allowed_length = trident::cfg_int(v, "gemma.dry-allowed-length");
    params.sampling.dry_penalty_last_n = trident::cfg_int(v, "gemma.dry-penalty-last-n");
    params.sampling.adaptive_target = trident::cfg_float(v, "gemma.adaptive-target");
    params.sampling.adaptive_decay = trident::cfg_float(v, "gemma.adaptive-decay");
    params.sampling.mirostat = trident::cfg_int(v, "gemma.mirostat");
    params.sampling.top_n_sigma = trident::cfg_float(v, "gemma.top-n-sigma");
    params.sampling.mirostat_tau = trident::cfg_float(v, "gemma.mirostat-tau");
    params.sampling.mirostat_eta = trident::cfg_float(v, "gemma.mirostat-eta");
    params.sampling.ignore_eos = trident::cfg_on(v, "gemma.ignore-eos");
    params.sampling.timing_per_token = trident::cfg_on(v, "gemma.timing-per-token");
    params.sampling.backend_sampling = trident::cfg_on(v, "gemma.backend-sampling");
}

static common_params default_params() {
    return {};
}

struct Gemma {
    common_init_result_ptr llama;
    llama_model * model = nullptr;
    llama_context * lctx = nullptr;
    const llama_vocab * vocab = nullptr;
    common_sampler * smpl = nullptr;
    llama_batch batch{};
    int n_batch = 512;
    int n_predict = 2048;
    llama_pos n_past = 0;
    mtmd::context_ptr mtmd_ctx;
    mtmd::bitmaps pending_media;
    mtmd_helper_init_opt media_opt = mtmd_helper_init_opt_default();

    explicit Gemma(common_params & params)
        : llama(common_init_from_params(params)), n_batch(params.n_batch), n_predict(params.n_predict) {
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

    static int b64_value(unsigned char c) {
        if (c >= 'A' && c <= 'Z') return c - 'A';
        if (c >= 'a' && c <= 'z') return c - 'a' + 26;
        if (c >= '0' && c <= '9') return c - '0' + 52;
        if (c == '+') return 62;
        if (c == '/') return 63;
        return -1;
    }

    static std::vector<unsigned char> b64_decode(const std::string & text) {
        std::vector<unsigned char> out;
        int value = 0, bits = 0;
        for (unsigned char c : text) {
            if (c == '\n' || c == '\r' || c == ' ' || c == '\t') continue;
            if (c == '=') break;
            const int digit = b64_value(c);
            if (digit < 0) throw std::runtime_error("gemma.image is not base64");
            value = (value << 6) | digit;
            bits += 6;
            if (bits >= 8) {
                bits -= 8;
                out.push_back((unsigned char)((value >> bits) & 0xFF));
            }
        }
        return out;
    }

    void eval_text(const std::string & text) {
        const auto tokens = common_tokenize(lctx, text, false, true);
        for (size_t i = 0; i < tokens.size();) {
            const size_t n = std::min(size_t(n_batch), tokens.size() - i);
            common_batch_clear(batch);
            for (size_t j = 0; j < n; ++j)
                common_batch_add(batch, tokens[i + j], n_past++, {0}, i + j + 1 == tokens.size());
            if (llama_decode(lctx, batch) != 0) throw std::runtime_error("llama_decode failed");
            i += n;
        }
    }

    void eval_media(const std::string & formatted) {
        mtmd::input_chunks chunks(mtmd_input_chunks_init());
        mtmd_input_text text{formatted.data(), formatted.size(), false, true};
        auto bitmaps = pending_media.c_ptr();
        const int32_t tok = mtmd_tokenize(mtmd_ctx.get(), chunks.ptr.get(), &text, bitmaps.data(), bitmaps.size());
        if (tok != 0) throw std::runtime_error("mtmd_tokenize failed (" + std::to_string(tok) + ")");
        pending_media.entries.clear();
        llama_pos new_n_past = n_past;
        const int32_t res = mtmd_helper_eval_chunks(mtmd_ctx.get(), lctx, chunks.ptr.get(), n_past, 0, n_batch, true, &new_n_past);
        if (res != 0) throw std::runtime_error("mtmd_helper_eval_chunks failed (" + std::to_string(res) + ")");
        n_past = new_n_past;
    }

    void reset() {
        llama_memory_clear(llama_get_memory(lctx), true);
        n_past = 0;
        common_sampler_reset(smpl);
        pending_media.entries.clear();
    }

    std::string generate(int max_tokens, const std::function<void(const std::string &)> & on_piece) {
        std::string out;
        for (int i = 0; i < max_tokens; ++i) {
            const llama_token id = common_sampler_sample(smpl, lctx, -1);
            common_sampler_accept(smpl, id, true);
            if (llama_vocab_is_eog(vocab, id)) break;
            const auto piece = common_token_to_piece(lctx, id);
            out += piece;
            if (!piece.empty() && on_piece) on_piece(piece);
            common_batch_clear(batch);
            common_batch_add(batch, id, n_past++, {0}, true);
            if (llama_decode(lctx, batch) != 0) throw std::runtime_error("llama_decode failed");
        }
        return out;
    }

    static bool filled(const std::string & text) {
        for (unsigned char c : text)
            if (c != ' ' && c != '\n' && c != '\r' && c != '\t') return true;
        return false;
    }

    std::string answer(const std::string & text, const std::string & image, int max_tokens,
                       const std::function<void(const std::string &)> & on_piece = {}) {
        reset();
        if (!filled(image)) eval_text(text);
        else {
            const auto bytes = b64_decode(image);
            auto res = mtmd_helper_bitmap_init_from_buf(mtmd_ctx.get(), bytes.data(), bytes.size(), false, media_opt);
            if (!res.bitmap) throw std::runtime_error("gemma.image is not a bitmap");
            pending_media.entries.emplace_back(res.bitmap);
            eval_media(text);
        }
        return generate(max_tokens, on_piece);
    }
};

constexpr std::uintmax_t kPromptCap = 16000000;

std::filesystem::path resident_pid_path;

void remove_resident_pid() {
    if (resident_pid_path.empty()) return;
    std::error_code ec;
    std::filesystem::remove(resident_pid_path, ec);
    std::filesystem::remove(resident_pid_path.parent_path() / "gemma.busy", ec);
    resident_pid_path.clear();
}

bool is_file(const std::filesystem::path & path) {
    std::error_code ec;
    return std::filesystem::is_regular_file(path, ec);
}

void publish(const std::filesystem::path & path, const std::string & body) {
    auto tmp = path.parent_path() / (path.filename().string() + ".tmp");
    trident::write_named(tmp, body);
    std::error_code ec;
    std::filesystem::remove(path, ec);
    std::filesystem::rename(tmp, path, ec);
    if (ec) trident::fail("cannot publish " + trident::path_u8(path));
}

std::string one_line(std::string text) {
    for (char & c : text) {
        if (c == '\n' || c == '\r' || c == '\t') c = ' ';
    }
    if (text.empty()) text = "failed";
    if (text.size() > 400) text.resize(400);
    return text;
}

bool decimal_id(const std::string & id) {
    if (id.empty() || id.size() > 32) return false;
    for (unsigned char c : id)
        if (c < '0' || c > '9') return false;
    return true;
}

std::string trim_cr(std::string text) {
    if (!text.empty() && text.back() == '\r') text.pop_back();
    return text;
}

bool hex64(const std::string & text) {
    if (text.size() != 64) return false;
    for (unsigned char c : text) {
        if (c >= '0' && c <= '9') continue;
        if (c >= 'a' && c <= 'f') continue;
        return false;
    }
    return true;
}

std::string loaded_fingerprint(const std::filesystem::path & path, unsigned long pid) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return {};
    std::string lines[3];
    int count = 0;
    std::string line;
    while (count < 3 && std::getline(in, line)) lines[count++] = trim_cr(line);
    if (count < 2) return {};
    char * end = nullptr;
    const unsigned long have = std::strtoul(lines[0].c_str(), &end, 10);
    if (end == lines[0].c_str() || *end || have != pid) return {};
    if (!hex64(lines[1])) return {};
    return lines[1];
}

void replace_pid_file(const std::filesystem::path & path, const std::string & body) {
    const auto tmp = path.parent_path() / "gemma.pid.tmp";
    trident::write_named(tmp, body);
    if (!MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        std::error_code ec;
        std::filesystem::remove(tmp, ec);
        trident::fail("cannot publish " + trident::path_u8(path));
    }
}

void hold_ready(unsigned long pid, std::string & loaded) {
    std::ifstream in(resident_pid_path, std::ios::binary);
    if (!in) return;
    std::string lines[3];
    int count = 0;
    std::string line;
    while (count < 3 && std::getline(in, line)) lines[count++] = trim_cr(line);
    in.close();
    if (count < 1) return;
    char * end = nullptr;
    const unsigned long have = std::strtoul(lines[0].c_str(), &end, 10);
    if (end == lines[0].c_str() || *end || have != pid) return;
    if (count < 2 || !hex64(lines[1])) return;
    if (loaded.empty()) loaded = lines[1];
    else if (lines[1] != loaded) return;
    if (count >= 3 && lines[2] == "ready") return;
    replace_pid_file(resident_pid_path, std::to_string(pid) + "\n" + loaded + "\nready\n");
}

std::string first_line(const std::filesystem::path & path, bool & got_nl) {
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

void set_busy(const std::filesystem::path & dir, bool on) {
    const auto path = dir / "gemma.busy";
    if (!on) {
        std::error_code ec;
        std::filesystem::remove(path, ec);
        return;
    }
    trident::write_named(path, "1\n");
}

struct Reply {
    HANDLE file = INVALID_HANDLE_VALUE;
    std::filesystem::path path;
    bool opened = false;

    ~Reply() { close(); }

    void close() {
        if (file != INVALID_HANDLE_VALUE) {
            CloseHandle(file);
            file = INVALID_HANDLE_VALUE;
        }
    }

    void open(const std::filesystem::path & target) {
        close();
        path = target;
        file = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE) throw std::runtime_error("cannot write gemma.response.txt");
        opened = true;
    }

    void raw(const std::string & bytes) {
        if (file == INVALID_HANDLE_VALUE) throw std::runtime_error("cannot write gemma.response.txt");
        DWORD wrote = 0;
        if (!WriteFile(file, bytes.data(), (DWORD)bytes.size(), &wrote, nullptr) || wrote != bytes.size())
            throw std::runtime_error("cannot write gemma.response.txt");
        if (!FlushFileBuffers(file)) throw std::runtime_error("cannot write gemma.response.txt");
    }
};

bool parse_turn(const std::string & body, std::string & id, std::string & image, std::string & text, std::string & err) {
    const auto nl1 = body.find('\n');
    if (nl1 == std::string::npos) {
        id = trim_cr(body);
        err = "bad prompt";
        return false;
    }
    id = trim_cr(body.substr(0, nl1));
    const auto rest = body.substr(nl1 + 1);
    const auto nl2 = rest.find('\n');
    if (nl2 == std::string::npos) {
        err = "bad prompt";
        return false;
    }
    const auto len_line = trim_cr(rest.substr(0, nl2));
    if (len_line.empty() || len_line.size() > 8) {
        err = "bad image length";
        return false;
    }
    for (unsigned char c : len_line) {
        if (c < '0' || c > '9') {
            err = "bad image length";
            return false;
        }
    }
    char * end = nullptr;
    const unsigned long n = std::strtoul(len_line.c_str(), &end, 10);
    if (end == len_line.c_str() || *end) {
        err = "bad image length";
        return false;
    }
    const auto after = rest.substr(nl2 + 1);
    if (after.size() < n) {
        err = "short image";
        return false;
    }
    image = after.substr(0, n);
    text = after.substr(n);
    if (text.empty()) {
        err = "empty";
        return false;
    }
    return true;
}

void reply_err(const std::filesystem::path & dir, const std::string & id, const std::string & msg) {
    const auto line = decimal_id(id) ? id : std::string("0");
    publish(dir / "gemma.response.txt", line + "\nerr " + one_line(msg) + "\n");
}

void handle_prompt(const std::filesystem::path & dir, Gemma & gemma) {
    const auto path = dir / "gemma.prompt.txt";
    if (!is_file(path)) return;
    std::error_code ec;
    const auto size = std::filesystem::file_size(path, ec);
    if (ec) return;
    if (size > kPromptCap) {
        bool nl = false;
        auto id = first_line(path, nl);
        std::filesystem::remove(path, ec);
        std::fprintf(stderr, "resident error prompt too long\n");
        std::fflush(stderr);
        reply_err(dir, id, "prompt too long");
        return;
    }
    std::ifstream in(path, std::ios::binary);
    if (!in) return;
    std::string body((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    in.close();
    std::filesystem::remove(path, ec);
    if (ec) return;
    std::string id, image, text, err;
    if (!parse_turn(body, id, image, text, err) || !decimal_id(id)) {
        if (!decimal_id(id)) {
            std::fprintf(stderr, "resident error bad prompt id\n");
            std::fflush(stderr);
            reply_err(dir, id, err.empty() ? "bad prompt id" : err);
            return;
        }
        std::fprintf(stderr, "resident error %s\n", one_line(err).c_str());
        std::fflush(stderr);
        reply_err(dir, id, err);
        return;
    }
    Reply reply;
    try {
        set_busy(dir, true);
        reply.open(dir / "gemma.response.txt");
        reply.raw(id + "\n");
        const auto t0 = std::chrono::steady_clock::now();
        gemma.answer(text, image, gemma.n_predict, [&](const std::string & piece) {
            std::string blob = "." + std::to_string(piece.size()) + "\n";
            blob += piece;
            reply.raw(blob);
        });
        reply.raw("ok\n");
        reply.close();
        const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
        std::fprintf(stderr, "resident generate %lld ms\n", static_cast<long long>(ms));
        std::fflush(stderr);
    } catch (const std::exception & ex) {
        const auto msg = one_line(ex.what());
        std::fprintf(stderr, "resident error %s\n", msg.c_str());
        std::fflush(stderr);
        try {
            if (reply.opened) reply.raw("err " + msg + "\n");
            else reply_err(dir, id, msg);
        } catch (const std::exception & write_ex) {
            std::fprintf(stderr, "resident error %s\n", one_line(write_ex.what()).c_str());
            std::fflush(stderr);
        }
    } catch (...) {
        std::fprintf(stderr, "resident error failed\n");
        std::fflush(stderr);
        try {
            if (reply.opened) reply.raw("err failed\n");
            else reply_err(dir, id, "failed");
        } catch (...) {
        }
    }
    reply.close();
    set_busy(dir, false);
}

int serve(Gemma & gemma) {
    const auto dir = std::filesystem::current_path();
    const auto pid = GetCurrentProcessId();
    resident_pid_path = dir / "gemma.pid";
    std::atexit(remove_resident_pid);
    if (is_file(dir / "gemma.stop")) {
        std::error_code ec;
        std::filesystem::remove(dir / "gemma.stop", ec);
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
        handle_prompt(dir, gemma);
        if (is_file(dir / "gemma.stop")) {
            std::error_code ec;
            std::filesystem::remove(dir / "gemma.stop", ec);
            std::fprintf(stderr, "resident stop pid %lu\n", static_cast<unsigned long>(GetCurrentProcessId()));
            std::fflush(stderr);
            remove_resident_pid();
            return 0;
        }
        Sleep(20);
    }
}

} // namespace

int main(int argc, char ** argv) {
    const char * file = nullptr;
    bool resident = false;
    if (argc == 2 && argv[1] && argv[1][0]) {
        file = argv[1];
    } else if (argc == 3 && argv[1] && std::string(argv[1]) == "--resident" && argv[2] && argv[2][0]) {
        resident = true;
        file = argv[2];
    } else {
        trident::fail("usage: gemma-brain.exe file.txt | gemma-brain.exe --resident file.txt");
    }
    if (resident) std::setvbuf(stderr, nullptr, _IONBF, 0);
    char * args[] = {argv[0], const_cast<char *>(file)};
    const auto values = trident::load_settings(2, args);
    ggml_time_init();
    common_init();
    common_params params = default_params();
    apply_config(params, values);
    const bool timings = trident::cfg_on(values, "gemma.mmproj-timings");
    ggml_backend_load_all();
    require_gpu(params.main_gpu);
    const auto load_t0 = std::chrono::steady_clock::now();
    Gemma gemma(params);
    gemma.open_mmproj(params, timings);
    if (!resident) {
        try {
            trident::write_output("gemma", gemma.answer(trident::cfg_key(values, "gemma.text"), trident::cfg_key(values, "gemma.image"), params.n_predict));
        } catch (const std::exception & ex) {
            std::fprintf(stderr, "error: %s\n", ex.what());
            return 1;
        }
        return 0;
    }
    const auto load_ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - load_t0).count();
    std::fprintf(stderr, "resident load %lld ms\n", static_cast<long long>(load_ms));
    std::fflush(stderr);
    return serve(gemma);
}
