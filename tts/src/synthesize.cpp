#include "cli.hpp"
#include "session.hpp"
#include "stream_writer.hpp"

#include <chrono>

namespace tts {

Speech run(const Runtime& runtime, const EngineKnobs& knobs, const std::string& text, int chunk_chars, float quiet_amp2, const AudioSink& sink) {
    Session session(runtime, knobs, chunk_chars, quiet_amp2);
    return session.synthesize(text, sink);
}

int run_job(const Args& args, const EngineKnobs& knobs, int chunk_chars) {
    for (const auto* key : {"--model", "--s3gen-gguf", "--reference", "--text-file"}) require_file(args, key);
    const std::string text = read_text(args.at("--text-file"));
    const Runtime runtime = runtime_from(args);
    const auto started = std::chrono::steady_clock::now();
    Session session(runtime, knobs, chunk_chars, kQuietAmp2);
    StreamingWavWriter writer(args.at("--output"));
    const Speech speech = session.synthesize(text,
        [&](const float* samples, size_t count) { writer.push(samples, count); });
    writer.finish();
    const double total_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
    print_done(speech, total_ms, runtime, knobs, chunk_chars);
    return 0;
}

} // namespace tts
