#pragma once

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <string>
#include <windows.h>

namespace trident {

inline void fail(const std::string & text) {
    std::fprintf(stderr, "%s\n", text.c_str());
    std::exit(2);
}

inline std::filesystem::path exe_dir() {
    wchar_t buf[MAX_PATH];
    const DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    return std::filesystem::path(buf, buf + n).parent_path();
}

inline std::string path_u8(const std::filesystem::path & path) {
    return path.u8string();
}

inline std::filesystem::path settings_file;

inline std::map<std::string, std::string> read_settings(const std::filesystem::path & file) {
    std::ifstream in(file, std::ios::binary);
    if (!in) fail("cannot read " + path_u8(file));
    std::map<std::string, std::string> out;
    std::string line;
    bool first = true;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (first) {
            first = false;
            if (line.size() >= 3 && (unsigned char)line[0] == 0xEF && (unsigned char)line[1] == 0xBB && (unsigned char)line[2] == 0xBF)
                line.erase(0, 3);
        }
        std::size_t i = 0;
        while (i < line.size() && (line[i] == ' ' || line[i] == '\t')) ++i;
        if (i >= line.size() || line[i] == '#') continue;
        line = line.substr(i);
        if (line.size() >= 2 && line.compare(line.size() - 2, 2, "<<") == 0) {
            auto key = line.substr(0, line.size() - 2);
            while (!key.empty() && (key.back() == ' ' || key.back() == '\t')) key.pop_back();
            std::string body;
            while (std::getline(in, line)) {
                if (!line.empty() && line.back() == '\r') line.pop_back();
                if (line == "<<") break;
                if (!body.empty()) body.push_back('\n');
                body += line;
            }
            out[key] = body;
            continue;
        }
        const auto sp = line.find(' ');
        if (sp == std::string::npos) out[line] = "";
        else out[line.substr(0, sp)] = line.substr(sp + 1);
    }
    return out;
}

inline std::map<std::string, std::string> load_settings(int argc, char ** argv) {
    if (argc != 2 || !argv[1] || !argv[1][0]) fail("usage: program file.txt");
    settings_file = std::filesystem::absolute(std::filesystem::u8path(argv[1]));
    if (!std::filesystem::is_regular_file(settings_file)) fail("missing " + path_u8(settings_file));
    return read_settings(settings_file);
}

inline const std::string & need(const std::map<std::string, std::string> & values, const std::string & key) {
    const auto it = values.find(key);
    if (it == values.end() || it->second.empty()) fail("settings missing " + key);
    return it->second;
}

inline const std::string & cfg_key(const std::map<std::string, std::string> & values, const std::string & key) {
    const auto it = values.find(key);
    if (it == values.end()) fail("settings missing " + key);
    return it->second;
}

inline std::filesystem::path cfg_path(const std::map<std::string, std::string> & values, const std::string & key) {
    if (settings_file.empty()) fail("settings missing");
    return settings_file.parent_path() / std::filesystem::u8path(need(values, key));
}

inline bool cfg_on(const std::map<std::string, std::string> & values, const std::string & key) {
    const auto & text = need(values, key);
    if (text == "on") return true;
    if (text == "off") return false;
    fail("settings " + key + " must be on or off");
}

inline int cfg_int(const std::map<std::string, std::string> & values, const std::string & key) {
    const auto & text = need(values, key);
    char * end = nullptr;
    const long value = std::strtol(text.c_str(), &end, 10);
    if (end == text.c_str() || *end) fail("settings " + key + " must be an integer");
    return (int)value;
}

inline float cfg_float(const std::map<std::string, std::string> & values, const std::string & key) {
    const auto & text = need(values, key);
    char * end = nullptr;
    const float value = std::strtof(text.c_str(), &end);
    if (end == text.c_str() || *end) fail("settings " + key + " must be a number");
    return value;
}

inline std::filesystem::path reserve_output(const std::string & role) {
    SYSTEMTIME st;
    GetLocalTime(&st);
    char stamp[16];
    std::snprintf(stamp, sizeof(stamp), "%02u-%02u-%02u-%03u", st.wHour, st.wMinute, st.wSecond, st.wMilliseconds);
    const auto dir = std::filesystem::current_path();
    for (int n = 0; n < 100000; ++n) {
        char name[160];
        std::snprintf(name, sizeof(name), "%s_%s_out_%03d.txt", stamp, role.c_str(), n);
        auto path = dir / name;
        HANDLE handle = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (handle == INVALID_HANDLE_VALUE) {
            if (GetLastError() == ERROR_FILE_EXISTS) continue;
            fail("cannot create " + path_u8(path));
        }
        CloseHandle(handle);
        return path;
    }
    fail("output names exhausted");
    return {};
}

inline void write_output(const std::string & role, const std::string & body) {
    const auto path = reserve_output(role);
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out) fail("cannot write " + path_u8(path));
    out.write(body.data(), (std::streamsize)body.size());
    if (!out) fail("cannot write " + path_u8(path));
}

inline void write_named(const std::filesystem::path & path, const std::string & body) {
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out) fail("cannot write " + path_u8(path));
    out.write(body.data(), (std::streamsize)body.size());
    if (!out) fail("cannot write " + path_u8(path));
}

} // namespace trident
