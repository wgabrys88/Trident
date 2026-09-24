#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdio>
#include "common/config.h"
#include <filesystem>
#include <fstream>
#include <sstream>
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

std::filesystem::path ear_model() {
    wchar_t buf[MAX_PATH];
    const DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    const auto exe = std::filesystem::path(buf, buf + n).parent_path();
    return std::filesystem::weakly_canonical(exe / ".." / ".." / "models" / "ear" / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf");
}

void usage() {
    const auto model = ear_model();
    std::fprintf(stderr,
                 "usage: ear.exe transcribe <audio> [knobs]\n"
                 "       ear.exe --persist\n"
                 "       ear.exe --unload\n"
                 "       ear.exe help transcribe\n"
                 "example: ear.exe transcribe wav\\hello.wav --format text\n"
                 "Forwards arguments to ear\\nemo-speech.exe next to this executable.\n"
                 "transcribe with no --model uses %s\n"
                 "  transcribe <audio>\n"
                 "      Wav or other audio file to transcribe.\n"
                 "  --model <gguf>\n"
                 "      ASR GGUF. Default is models/ear/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf from the repo root.\n"
                 "  --language <code>\n"
                 "      Spoken language. Omit it to let the model decide.\n"
                 "  --device <cpu|cuda>\n"
                 "      Where the ASR graph runs. This install is the CPU build.\n"
                 "  --backend <name>\n"
                 "      Inference backend name compiled into nemo-speech.\n"
                 "  --concurrency <n>\n"
                 "      How many audio files to transcribe at once.\n"
                 "  --format text\n"
                 "      Output layout. text prints the transcript. Other names come from nemo-speech.\n"
                 "  --output <path>\n"
                 "      Write the transcript to this file instead of the console.\n"
                 "  --output-dir <dir>\n"
                 "      Directory for one transcript per input.\n"
                 "  --recursive\n"
                 "      Walk a directory of audio files.\n"
                 "  --word-times\n"
                 "      Include word timestamps.\n"
                 "  --vad-model <path>\n"
                 "      Voice-activity model. Empty uses the one packed with the ASR model.\n"
                 "  --vad-masking\n"
                 "      Drop non-speech before recognition.\n"
                 "  --diarize\n"
                 "      Split the transcript by speaker.\n"
                 "  --diar-model <path>\n"
                 "      Speaker model used when --diarize is set.\n"
                 "  --itn-model-dir <dir>\n"
                 "      Inverse-text-normalization models.\n"
                 "  --pnc-model <path>\n"
                 "      Punctuation and capitalization model.\n"
                 "  --no-punctuation\n"
                 "      Leave punctuation out of the transcript.\n"
                 "  --verbatim\n"
                 "      Keep hesitations and partial words.\n"
                 "  --stream\n"
                 "      Transcribe a growing audio stream.\n"
                 "  --endpointing\n"
                 "      Cut the stream when speech ends.\n"
                 "  --stop-history-eou-ms <ms>\n"
                 "      Silence, in milliseconds, that ends an utterance.\n"
                 "  --max-alternatives <n>\n"
                 "      How many transcript alternatives to return.\n"
                 "  --speech-context <words>\n"
                 "      Words to bias the decoder toward.\n"
                 "  --speech-context-boost <weight>\n"
                 "      How strongly --speech-context pulls the decoder.\n"
                 "  --profanity-filter\n"
                 "      Mask profanity in the transcript.\n"
                 "  --config <path>\n"
                 "      JSON or text config passed through to nemo-speech.\n"
                 "  --no-warmup\n"
                 "      Skip the warmup pass.\n"
                 "  --no-batching\n"
                 "      Decode one utterance at a time.\n"
                 "  --force\n"
                 "      Overwrite an existing --output file.\n"
                 "  --json\n"
                 "      Print a JSON transcript.\n"
                 "  --quiet\n"
                 "      Hide progress logs.\n"
                 "  --verbose\n"
                 "      Print extra logs.\n"
                 "  --live\n"
                 "      Print words as they are decoded.\n"
                 "  --translate-to <lang>\n"
                 "      Translate the transcript. Needs an NMT model.\n"
                 "  --nmt-model <path>\n"
                 "      Translation model used by --translate-to.\n"
                 "  --gpu / asr.backend.gpu\n"
                 "      GPU index when the ear build has a GPU backend.\n"
                 "  --chunk-sec / asr.streaming.chunk_size\n"
                 "      Streaming chunk length in seconds.\n"
                 "  --left-pad-sec / asr.streaming.ctc_left_padding\n"
                 "      Left context padded onto each chunk.\n"
                 "  --right-pad-sec / asr.streaming.ctc_right_padding\n"
                 "      Right context padded onto each chunk.\n"
                 "  --asr.streaming.rnnt_right_context\n"
                 "      Right context used by the RNN-T decoder.\n"
                 "  --asr.batching.enabled\n"
                 "      Turn micro-batching on or off.\n"
                 "  --asr.batching.max_batch_size\n"
                 "      Largest batch of utterances.\n"
                 "  --asr.batching.max_queue_delay_us\n"
                 "      How long a short batch may wait, in microseconds.\n"
                 "  --asr.batching.max_queue_depth\n"
                 "      How many utterances may wait.\n"
                 "  --asr.batching.ingress_cohort_delay_us\n"
                 "      Extra wait so nearby requests share a batch.\n"
                 "  --asr.batching.state_arena_slots\n"
                 "      Streaming state slots kept alive.\n"
                 "  --asr.batching.offline_bucket_ms\n"
                 "      Length bucket for offline files, in milliseconds.\n"
                 "  --asr.decoder.kind\n"
                 "      Decoder implementation name.\n"
                 "  --lm-path <path>\n"
                 "      External language model.\n"
                 "  --lexicon <path>\n"
                 "      Pronunciation lexicon for the beam decoder.\n"
                 "  --tokenizer <path>\n"
                 "      Tokenizer paired with --lm-path.\n"
                 "  --beam-size <n>\n"
                 "      Beam width.\n"
                 "  --asr.decoder.beam_size_token\n"
                 "      Token beam width when it differs from --beam-size.\n"
                 "  --beam-threshold <score>\n"
                 "      Drop beam entries worse than the best by this much.\n"
                 "  --lm-weight <weight>\n"
                 "      Language-model score weight.\n"
                 "  --word-score <score>\n"
                 "      Bonus added per finished word.\n"
                 "  --max-boost <weight>\n"
                 "      Cap on speech-context boosting.\n"
                 "  --boosting-tree-alpha <value>\n"
                 "      How fast the boosting tree decays.\n"
                 "  --boosting-max-boost <weight>\n"
                 "      Maximum boost inside the tree.\n"
                 "  --boosting-depth-scaling <value>\n"
                 "      Scale the boost by phrase depth.\n"
                 "  --vad-onset <prob>\n"
                 "      Probability that starts a speech region.\n"
                 "  --vad-offset <prob>\n"
                 "      Probability that ends a speech region.\n"
                 "  --vad-pad-ms <ms>\n"
                 "      Padding kept around each speech region.\n"
                 "  --asr.vad.masker.pad_onset_ms <ms>\n"
                 "      Extra audio kept before speech onset.\n"
                 "  --asr.vad.masker.pad_offset_ms <ms>\n"
                 "      Extra audio kept after speech offset.\n"
                 "  --vad-min-duration-off-ms <ms>\n"
                 "      Shortest silence that splits speech.\n"
                 "  --vad-stddev-floor <value>\n"
                 "      Floor on the VAD noise estimate.\n"
                 "  --vad-mask-value <value>\n"
                 "      Sample value written over non-speech.\n"
                 "  --diar-preset <name>\n"
                 "      Named speaker-diarization preset.\n"
                 "  --diar-chunk <sec>\n"
                 "      Diarization chunk length.\n"
                 "  --diar-rc <sec>\n"
                 "      Diarization right context.\n"
                 "  --diar-lc <sec>\n"
                 "      Diarization left context.\n"
                 "  --diar-fifo <n>\n"
                 "      Speaker embedding queue length.\n"
                 "  --diar-spkcache <n>\n"
                 "      How many speakers to remember.\n"
                 "  --diar-update-period <n>\n"
                 "      Chunks between speaker-cache updates.\n"
                 "  --vad-based-eou\n"
                 "      End the utterance from VAD instead of a timer.\n"
                 "  --profanity-list <path>\n"
                 "      Word list used by --profanity-filter.\n"
                 "  --asr.postproc.cpu_workers <n>\n"
                 "      CPU workers for punctuation and text cleanup.\n"
                 "  --asr.postproc.max_queue_depth <n>\n"
                 "      How many transcripts may wait for cleanup.\n"
                 "Any other --asr.* or --nmt.* flag is forwarded as written.\n"
                 "Numeric defaults for those flags are the ones compiled into nemo-speech.\n"
                 "ear.exe help transcribe prints that compiled list.\n",
                 model.string().c_str());
}

} // namespace

int main(int argc, char** argv) {
    if (argc >= 2 && std::string(argv[1]) == "--unload") return trident::unload_named("ear");
    if (argc >= 2 && std::string(argv[1]) == "--persist") {
        const auto values = trident::load_trident();
        const auto request = trident::cfg_path(values, "ear.prompt-file");
        const auto response = trident::cfg_path(values, "ear.response-file");
        wchar_t buf[MAX_PATH];
        const DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
        const auto nemo = std::filesystem::path(buf, buf + n).parent_path() / "ear" / "nemo-speech.exe";
        trident::write_pid("ear");
        std::filesystem::remove(trident::slot("ear", "stop"));
        auto seen = std::filesystem::file_time_type::min();
        while (!std::filesystem::exists(trident::slot("ear", "stop"))) {
            if (std::filesystem::is_regular_file(request)) {
                const auto stamp = std::filesystem::last_write_time(request);
                if (stamp != seen) {
                    seen = stamp;
                    std::ifstream in(request);
                    std::stringstream buffer;
                    buffer << in.rdbuf();
                    const auto audio = buffer.str();
                    if (!audio.empty()) {
                        std::wstring command = quote(nemo.wstring()) + L" " + quote(widen("transcribe")) + L" " + quote(widen(audio))
                            + L" " + quote(widen("--model")) + L" " + quote(ear_model().wstring())
                            + L" " + quote(widen("--format")) + L" " + quote(widen("text"))
                            + L" " + quote(widen("--output")) + L" " + quote(response.wstring());
                        std::vector<wchar_t> mutable_command(command.begin(), command.end());
                        mutable_command.push_back(0);
                        STARTUPINFOW startup{};
                        startup.cb = sizeof(startup);
                        PROCESS_INFORMATION process{};
                        if (CreateProcessW(nemo.c_str(), mutable_command.data(), nullptr, nullptr, TRUE, 0, nullptr, nullptr, &startup, &process)) {
                            WaitForSingleObject(process.hProcess, INFINITE);
                            CloseHandle(process.hThread);
                            CloseHandle(process.hProcess);
                        }
                    }
                }
            }
            Sleep(200);
        }
        std::filesystem::remove(trident::slot("ear", "pid"));
        std::filesystem::remove(trident::slot("ear", "stop"));
        return 0;
    }
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
    bool transcribe = false;
    bool has_model = false;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "transcribe") transcribe = true;
        if (arg == "--model") has_model = true;
    }
    std::wstring command = quote(nemo.wstring());
    for (int i = 1; i < argc; ++i) command += L" " + quote(widen(argv[i]));
    if (transcribe && !has_model) command += L" " + quote(widen("--model")) + L" " + quote(ear_model().wstring());
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
