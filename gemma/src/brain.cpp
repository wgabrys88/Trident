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
    tensor_split(params, trident::cfg_opt(v, "gemma.tensor-split"));
    params.split_mode = pick<llama_split_mode>(trident::need(v, "gemma.split"), {{"none", LLAMA_SPLIT_MODE_NONE}, {"layer", LLAMA_SPLIT_MODE_LAYER}, {"row", LLAMA_SPLIT_MODE_ROW}, {"tensor", LLAMA_SPLIT_MODE_TENSOR}}, "gemma.split");
    params.load_mode = pick<llama_load_mode>(trident::need(v, "gemma.load-mode"), {{"auto", LLAMA_LOAD_MODE_AUTO}, {"none", LLAMA_LOAD_MODE_NONE}, {"mmap", LLAMA_LOAD_MODE_MMAP}, {"mlock", LLAMA_LOAD_MODE_MLOCK}, {"mmap-mlock", LLAMA_LOAD_MODE_MMAP_MLOCK}, {"direct-io", LLAMA_LOAD_MODE_DIRECT_IO}}, "gemma.load-mode");
    params.lazy_mode = pick<llama_lazy_mode>(trident::need(v, "gemma.lazy-mode"), {{"off", LLAMA_LAZY_MODE_OFF}, {"auto", LLAMA_LAZY_MODE_AUTO}, {"on", LLAMA_LAZY_MODE_ON}}, "gemma.lazy-mode");
    params.numa = pick<ggml_numa_strategy>(trident::need(v, "gemma.numa"), {{"disabled", GGML_NUMA_STRATEGY_DISABLED}, {"distribute", GGML_NUMA_STRATEGY_DISTRIBUTE}, {"isolate", GGML_NUMA_STRATEGY_ISOLATE}, {"numactl", GGML_NUMA_STRATEGY_NUMACTL}, {"mirror", GGML_NUMA_STRATEGY_MIRROR}}, "gemma.numa");
    const int threads = trident::cfg_int(v, "gemma.threads");
    if (threads > 0) params.cpuparams.n_threads = threads;
    const int threads_batch = trident::cfg_int(v, "gemma.threads-batch");
    if (threads_batch > 0) params.cpuparams_batch.n_threads = threads_batch;
    else if (threads > 0) params.cpuparams_batch.n_threads = threads;
    auto priority = [](const std::string & value, const char * name) {
        return pick<ggml_sched_priority>(value, {{"low", GGML_SCHED_PRIO_LOW}, {"normal", GGML_SCHED_PRIO_NORMAL}, {"medium", GGML_SCHED_PRIO_MEDIUM}, {"high", GGML_SCHED_PRIO_HIGH}, {"realtime", GGML_SCHED_PRIO_REALTIME}}, name);
    };
    params.cpuparams.priority = priority(trident::need(v, "gemma.priority"), "gemma.priority");
    params.cpuparams_batch.priority = priority(trident::need(v, "gemma.priority-batch"), "gemma.priority-batch");
    params.cpuparams.poll = (uint32_t)trident::cfg_int(v, "gemma.poll");
    params.cpuparams_batch.poll = (uint32_t)trident::cfg_int(v, "gemma.poll-batch");
    params.cpuparams.strict_cpu = trident::cfg_on(v, "gemma.strict-cpu");
    params.cpuparams_batch.strict_cpu = trident::cfg_on(v, "gemma.strict-cpu-batch");
    cpu_mask(params.cpuparams, trident::cfg_opt(v, "gemma.cpu-mask"));
    cpu_mask(params.cpuparams_batch, trident::cfg_opt(v, "gemma.cpu-mask-batch"));
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
    const auto & seed = trident::need(v, "gemma.seed");
    params.sampling.seed = seed == "-1" ? LLAMA_DEFAULT_SEED : (uint32_t)std::strtoul(seed.c_str(), nullptr, 10);
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
    common_params p;
    const int nth = host_thread_count();
    p.cpuparams.n_threads = nth;
    p.cpuparams_batch.n_threads = nth;
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

    void eval_text(const std::string & text, bool bos) {
        const auto tokens = common_tokenize(lctx, text, bos, true);
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
        mtmd::input_chunks chunks(mtmd_input_chunks_init());
        mtmd_input_text text{formatted.data(), formatted.size(), true, true};
        auto bitmaps = pending_media.c_ptr();
        const int32_t tok = mtmd_tokenize(mtmd_ctx.get(), chunks.ptr.get(), &text, bitmaps.data(), bitmaps.size());
        if (tok != 0) die_fmt("mtmd_tokenize failed (%d)", tok);
        pending_media.entries.clear();
        llama_pos new_n_past = n_past;
        const int32_t res = mtmd_helper_eval_chunks(mtmd_ctx.get(), lctx, chunks.ptr.get(), n_past, 0, n_batch, true, &new_n_past);
        if (res != 0) die_fmt("mtmd_helper_eval_chunks failed (%d)", res);
        n_past = new_n_past;
    }

    void reset() {
        llama_memory_clear(llama_get_memory(lctx), true);
        n_past = 0;
        common_sampler_reset(smpl);
        pending_media.entries.clear();
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

static std::string trim(std::string text) {
    auto sp = [](unsigned char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; };
    while (!text.empty() && sp((unsigned char)text.front())) text.erase(text.begin());
    while (!text.empty() && sp((unsigned char)text.back())) text.pop_back();
    return text;
}

static std::string tool_decls(const std::string & spec) {
    std::string blocks, name, description, params, fields;
    auto flush = [&]() {
        if (name.empty()) return;
        std::string note = description;
        if (!params.empty()) note += (note.empty() ? std::string() : " ") + params;
        blocks += "<|tool>declaration:" + name + (fields.empty() ? "{}" : "{" + fields + "}") + "<tool|>" + note;
        name.clear();
        description.clear();
        params.clear();
        fields.clear();
    };
    std::stringstream stream(spec);
    std::string line;
    while (std::getline(stream, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        line = trim(line);
        if (line.empty()) {
            flush();
            continue;
        }
        if (name.empty()) {
            name = line;
            continue;
        }
        const auto colon = line.find(':');
        if (colon == std::string::npos) {
            description = line;
            continue;
        }
        const auto key = trim(line.substr(0, colon));
        const auto note = trim(line.substr(colon + 1));
        if (!fields.empty()) fields += ",";
        fields += key + ":<|\"|>string<|\"|>";
        if (!params.empty()) params += " ";
        params += key + " is " + note;
    }
    flush();
    return blocks;
}

static std::string system_turn(const std::string & spec, const std::string & instructions) {
    return "<|turn>system\n<|think|>" + trim(instructions) + "\n" + tool_decls(spec) + "<turn|>\n";
}

static std::string user_turn(const std::string & text) {
    return "<|turn>user\n" + trim(text) + "<turn|>\n";
}

struct Call {
    std::string name;
    std::map<std::string, std::string> args;
};

static std::vector<Call> parse_calls(const std::string & text) {
    std::vector<Call> out;
    const std::string open = "<|tool_call>call:";
    const std::string q = "<|\"|>";
    for (size_t at = 0; (at = text.find(open, at)) != std::string::npos;) {
        at += open.size();
        const auto brace = text.find('{', at);
        if (brace == std::string::npos) break;
        const auto end_tool = text.find("}<tool_call|>", brace);
        const auto end_turn = text.find("}<turn|>", brace);
        const auto end = std::min(end_tool, end_turn);
        if (end == std::string::npos) break;
        Call call;
        call.name = trim(text.substr(at, brace - at));
        const auto body = text.substr(brace + 1, end - brace - 1);
        for (size_t i = 0; i < body.size();) {
            const auto colon = body.find(':', i);
            if (colon == std::string::npos) break;
            auto key = trim(body.substr(i, colon - i));
            const auto comma = key.rfind(',');
            if (comma != std::string::npos) key = trim(key.substr(comma + 1));
            size_t v = colon + 1;
            if (body.compare(v, q.size(), q) == 0) {
                v += q.size();
                const auto stop = body.find(q, v);
                if (stop == std::string::npos) break;
                call.args[key] = trim(body.substr(v, stop - v));
                i = stop + q.size();
            } else {
                auto stop = body.find(',', v);
                if (stop == std::string::npos) stop = body.size();
                call.args[key] = trim(body.substr(v, stop - v));
                i = stop + 1;
            }
        }
        out.push_back(std::move(call));
        at = end + 1;
    }
    return out;
}

static std::string tool_response(const std::string & name, const std::map<std::string, std::string> & fields) {
    std::string parts;
    for (const auto & field : fields) {
        if (!parts.empty()) parts += ",";
        parts += field.first + ":<|\"|>" + field.second + "<|\"|>";
    }
    return "<|tool_response>response:" + name + "{" + parts + "}<tool_response|>";
}

static std::vector<std::string> load_history(const std::filesystem::path & path) {
    std::vector<std::string> history;
    std::ifstream in(path, std::ios::binary);
    std::stringstream buffer;
    buffer << in.rdbuf();
    const auto raw = buffer.str();
    const std::string marker = "<|turn>";
    size_t start = 0;
    while (start <= raw.size()) {
        const auto found = raw.find(marker, start);
        const auto part = raw.substr(start, found == std::string::npos ? std::string::npos : found - start);
        if (!trim(part).empty()) history.push_back(marker + part);
        if (found == std::string::npos) break;
        start = found + marker.size();
    }
    return history;
}

static void compact_history(std::vector<std::string> & history, int limit) {
    auto total = [&]() {
        size_t n = 0;
        for (const auto & part : history) n += part.size();
        return n;
    };
    while (history.size() > 1 && total() > (size_t)limit) history.erase(history.begin());
}

} // namespace

int main(int, char **) {
    ggml_time_init();
    common_init();
    const auto values = trident::load_trident();
    if (trident::cfg_on(values, "gemma.unload")) return trident::unload_named("gemma");
    if (trident::resident("gemma")) return 0;
    common_params params = default_params();
    apply_config(params, values);
    const bool timings = trident::cfg_on(values, "gemma.mmproj-timings");
    const int poll_ms = trident::cfg_int(values, "gemma.poll-ms");
    const int reset_every = trident::cfg_int(values, "gemma.reset-every");
    const int reset_ms = trident::cfg_int(values, "gemma.reset-ms");
    const auto request = trident::cfg_path(values, "gemma.prompt-file");
    const auto response = trident::cfg_path(values, "gemma.response-file");
    const auto history_path = trident::cfg_path(values, "gemma.history-file");
    const auto mouth = trident::cfg_path(values, "chatterbox.prompt-file");
    const int history_max = trident::cfg_int(values, "gemma.history-max");
    const auto tools = trident::cfg_opt(values, "gemma.tools");
    const auto instructions = trident::cfg_opt(values, "gemma.system");
    const auto image_key = trident::cfg_opt(values, "gemma.image");
    const auto image = image_key.empty() ? std::string() : trident::path_u8(trident::trident_file().parent_path() / std::filesystem::u8path(image_key));
    const char * model_open = "<|turn>model\n";
    ggml_backend_load_all();
    require_gpu(params.main_gpu);
    Gemma gemma(params);
    if (!image.empty()) gemma.open_mmproj(params, timings);
    int served = 0;
    ULONGLONG reset_at = GetTickCount64();
    auto turn = [&](const std::string & heard) {
        const bool by_count = reset_every > 0 && served % reset_every == 0;
        const bool by_time = reset_ms > 0 && GetTickCount64() - reset_at >= (ULONGLONG)reset_ms;
        if (by_count || by_time) {
            gemma.reset();
            reset_at = GetTickCount64();
        }
        ++served;
        auto history = load_history(history_path);
        compact_history(history, history_max);
        const auto user = user_turn(heard);
        std::string prefix = system_turn(tools, instructions);
        for (const auto & part : history) prefix += part;
        prefix += user + model_open;
        auto once = [&](const std::string & text, bool first) {
            if (first && !image.empty()) {
                gemma.load_media(image.c_str());
                const std::string marker = mtmd_default_marker();
                gemma.eval_media(text.find(marker) == std::string::npos ? marker + text : text);
            } else gemma.eval_text(text, first);
            return gemma.generate(params.n_predict);
        };
        auto act = [&](const Call & call) {
            std::map<std::string, std::string> fields;
            if (call.name == "see") fields["image"] = image_key.empty() ? "none" : image_key;
            else if (call.name != "speak") fields["error"] = "unknown";
            else {
                const auto it = call.args.find("text");
                const auto words = it == call.args.end() ? std::string() : trim(it->second);
                if (words.empty()) fields["spoken"] = "0";
                else {
                    std::ofstream(mouth, std::ios::binary | std::ios::trunc) << words;
                    fields["spoken"] = "1";
                }
            }
            return fields;
        };
        auto latest = once(prefix, true);
        std::string body = latest;
        for (;;) {
            const auto calls = parse_calls(latest);
            if (calls.empty()) break;
            std::string extra;
            for (const auto & call : calls) extra += tool_response(call.name, act(call));
            body += extra;
            latest = once(extra, false);
            body += latest;
        }
        auto stored = body;
        while (!stored.empty() && (stored.back() == ' ' || stored.back() == '\t' || stored.back() == '\n' || stored.back() == '\r')) stored.pop_back();
        if (stored.size() < 8 || stored.compare(stored.size() - 8, 8, "<turn|>") != 0) stored += "<turn|>";
        history.push_back(user + model_open + stored + "\n");
        compact_history(history, history_max);
        {
            std::ofstream out(history_path, std::ios::binary | std::ios::trunc);
            for (const auto & part : history) out << part;
        }
        std::ofstream(response, std::ios::binary | std::ios::trunc) << body;
    };
    if (trident::cfg_on(values, "gemma.persist")) return trident::watch("gemma", request, poll_ms, turn);
    const auto prompt = trident::read_text(request);
    if (prompt.empty()) die("gemma.prompt-file is empty");
    turn(prompt);
    llama_perf_context_print(gemma.lctx);
    return 0;
}
