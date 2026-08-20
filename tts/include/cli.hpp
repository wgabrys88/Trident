#pragma once

#include "audio.hpp"

#include <string>
#include <unordered_map>
#include <vector>

namespace tts {

using Args = std::unordered_map<std::string, std::string>;

Args parse_args(int argc, char** argv, const std::vector<std::string>& required);
int parse_int(const Args& args, const std::string& key);
float parse_float(const Args& args, const std::string& key);
std::string read_text(const std::string& path);
void require_file(const Args& args, const std::string& key);
Runtime runtime_from(const Args& args);
void log(const std::string& line);
void print_done(const Speech& speech, double total_ms, const Runtime& runtime, const EngineKnobs& knobs, int chunk_chars);

}
