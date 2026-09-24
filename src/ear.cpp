#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdio>
#include <filesystem>
#include <string>
#include <vector>

namespace {

std::wstring widen(const std::string& text) {
    if (text.empty()) return {};
    const int n = MultiByteToWideChar(CP_UTF8, 0, text.data(), (int)text.size(), nullptr, 0);
    std::wstring out(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, text.data(), (int)text.size(), out.data(), n);
    return out;
}

std::wstring quote(const std::wstring& text) {
    std::wstring out = L"\"";
    for (wchar_t c : text) {
        if (c == L'"') out += L"\\\"";
        else out += c;
    }
    out += L'"';
    return out;
}

void usage() {
    std::fprintf(stderr,
                 "usage: ear.exe <nemo-speech arguments>\n"
                 "forwards every argument to build/bin/ear/nemo-speech.exe. nothing is inserted.\n"
                 "transcribe knobs:\n"
                 "  --model --language --device --backend --concurrency --format --output --output-dir\n"
                 "  --recursive --word-times --vad-model --vad-masking --diarize --diar-model\n"
                 "  --itn-model-dir --pnc-model --no-punctuation --verbatim --stream\n"
                 "  --endpointing --stop-history-eou-ms --max-alternatives --speech-context\n"
                 "  --speech-context-boost --profanity-filter --config --no-warmup --no-batching --force\n"
                 "  --json --quiet --verbose --live --translate-to --nmt-model\n"
                 "asr engine keys (dotted form always works; alias shown when one exists):\n"
                 "  --gpu / asr.backend.gpu\n"
                 "  --chunk-sec / asr.streaming.chunk_size\n"
                 "  --left-pad-sec / asr.streaming.ctc_left_padding\n"
                 "  --right-pad-sec / asr.streaming.ctc_right_padding\n"
                 "  --asr.streaming.rnnt_right_context\n"
                 "  --asr.batching.enabled --asr.batching.max_batch_size --asr.batching.max_queue_delay_us\n"
                 "  --asr.batching.max_queue_depth --asr.batching.ingress_cohort_delay_us\n"
                 "  --asr.batching.state_arena_slots --asr.batching.offline_bucket_ms\n"
                 "  --asr.decoder.kind --lm-path --lexicon --tokenizer --beam-size\n"
                 "  --asr.decoder.beam_size_token --beam-threshold --lm-weight --word-score\n"
                 "  --max-boost --boosting-tree-alpha --boosting-max-boost --boosting-depth-scaling\n"
                 "  --vad-onset --vad-offset --vad-pad-ms --asr.vad.masker.pad_onset_ms\n"
                 "  --asr.vad.masker.pad_offset_ms --vad-min-duration-off-ms --vad-stddev-floor --vad-mask-value\n"
                 "  --diar-preset --diar-chunk --diar-rc --diar-lc --diar-fifo --diar-spkcache --diar-update-period\n"
                 "  --vad-based-eou --profanity-list --asr.postproc.cpu_workers --asr.postproc.max_queue_depth\n"
                 "any other --asr.* --nmt.* or nemo-speech flag is forwarded as written.\n"
                 "ear.exe help transcribe prints the flags compiled into the installed nemo-speech.\n");
}

} // namespace

int main(int argc, char** argv) {
    if (argc >= 2 && (std::string(argv[1]) == "-h" || std::string(argv[1]) == "--help") && argc == 2) {
        usage();
        return 0;
    }
    wchar_t buf[MAX_PATH];
    const DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    const auto nemo = std::filesystem::path(buf, buf + n).parent_path() / "ear" / "nemo-speech.exe";
    if (!std::filesystem::is_regular_file(nemo)) {
        std::fprintf(stderr, "ear error: missing %ls\n", nemo.c_str());
        return 1;
    }
    std::wstring command = quote(nemo.wstring());
    for (int i = 1; i < argc; ++i) command += L" " + quote(widen(argv[i]));
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(0);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(nemo.c_str(), mutable_command.data(), nullptr, nullptr, TRUE, 0, nullptr, nullptr, &startup, &process)) {
        std::fprintf(stderr, "ear error: CreateProcess failed (%lu)\n", GetLastError());
        return 1;
    }
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return (int)code;
}
