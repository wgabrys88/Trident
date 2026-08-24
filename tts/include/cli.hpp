#pragma once

#include "audio.hpp"

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

namespace tts {

using Args = std::unordered_map<std::string, std::string>;

Args parse_args(int argc, char** argv, const std::vector<std::string>& required);
int parse_int(const Args& args, const std::string& key);
float parse_float(const Args& args, const std::string& key);
Runtime runtime_from(const Args& args);
void set_request_id(std::uint64_t id);
void log(const std::string& line);

}
