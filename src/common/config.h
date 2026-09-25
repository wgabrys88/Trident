#pragma once

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <sstream>
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

inline std::filesystem::path trident_file() {
    auto dir = exe_dir();
    for (;;) {
        auto candidate = dir / "trident.txt";
        if (std::filesystem::is_regular_file(candidate)) return candidate;
        auto parent = dir.parent_path();
        if (parent == dir) return {};
        dir = parent;
    }
}

inline std::map<std::string, std::string> load_trident() {
    const auto file = trident_file();
    if (file.empty()) fail("trident.txt missing");
    std::ifstream in(file, std::ios::binary);
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

inline const std::string & need(const std::map<std::string, std::string> & values, const std::string & key) {
    const auto it = values.find(key);
    if (it == values.end() || it->second.empty()) fail("trident.txt missing " + key);
    return it->second;
}

inline std::string cfg_opt(const std::map<std::string, std::string> & values, const std::string & key) {
    const auto it = values.find(key);
    return it == values.end() ? std::string() : it->second;
}

inline std::filesystem::path cfg_path(const std::map<std::string, std::string> & values, const std::string & key) {
    const auto file = trident_file();
    if (file.empty()) fail("trident.txt missing");
    return file.parent_path() / std::filesystem::u8path(need(values, key));
}

inline bool cfg_on(const std::map<std::string, std::string> & values, const std::string & key) {
    const auto & text = need(values, key);
    if (text == "on") return true;
    if (text == "off") return false;
    fail("trident.txt " + key + " must be on or off");
}

inline int cfg_int(const std::map<std::string, std::string> & values, const std::string & key) {
    const auto & text = need(values, key);
    char * end = nullptr;
    const long value = std::strtol(text.c_str(), &end, 10);
    if (end == text.c_str() || *end) fail("trident.txt " + key + " must be an integer");
    return (int)value;
}

inline float cfg_float(const std::map<std::string, std::string> & values, const std::string & key) {
    const auto & text = need(values, key);
    char * end = nullptr;
    const float value = std::strtof(text.c_str(), &end);
    if (end == text.c_str() || *end) fail("trident.txt " + key + " must be a number");
    return value;
}

inline std::string read_text(const std::filesystem::path & path) {
    std::ifstream in(path, std::ios::binary);
    std::stringstream buffer;
    buffer << in.rdbuf();
    auto text = buffer.str();
    if (text.size() >= 3 && (unsigned char)text[0] == 0xEF && (unsigned char)text[1] == 0xBB && (unsigned char)text[2] == 0xBF)
        text.erase(0, 3);
    if (!text.empty() && text.back() == '\n') text.pop_back();
    if (!text.empty() && text.back() == '\r') text.pop_back();
    return text;
}

inline std::filesystem::path slot(const std::string & name, const char * kind) {
    const auto file = trident_file();
    if (file.empty()) fail("trident.txt missing");
    return file.parent_path() / (name + "." + kind);
}

inline DWORD read_pid(const std::string & name) {
    std::ifstream in(slot(name, "pid"));
    DWORD pid = 0;
    in >> pid;
    return pid;
}

inline bool pid_alive(DWORD pid) {
    if (!pid) return false;
    HANDLE handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!handle) return false;
    DWORD code = 0;
    GetExitCodeProcess(handle, &code);
    CloseHandle(handle);
    return code == STILL_ACTIVE;
}

inline bool resident(const std::string & name) { return pid_alive(read_pid(name)); }

inline void write_pid(const std::string & name) {
    std::ofstream out(slot(name, "pid"), std::ios::trunc);
    out << GetCurrentProcessId();
}

inline int unload_named(const std::string & name) {
    std::ofstream(slot(name, "stop"), std::ios::trunc) << "1";
    const DWORD pid = read_pid(name);
    const auto values = load_trident();
    const int tries = cfg_int(values, "runtime.unload-tries");
    const int wait_ms = cfg_int(values, "runtime.unload-wait-ms");
    for (int i = 0; i < tries && pid_alive(pid); ++i) Sleep(wait_ms);
    if (pid_alive(pid)) {
        HANDLE handle = OpenProcess(PROCESS_TERMINATE, FALSE, pid);
        if (handle) {
            TerminateProcess(handle, 0);
            CloseHandle(handle);
        }
    }
    std::filesystem::remove(slot(name, "pid"));
    std::filesystem::remove(slot(name, "stop"));
    return 0;
}

template <class F>
int watch(const std::string & name, const std::filesystem::path & request, int poll_ms, F fn) {
    write_pid(name);
    std::filesystem::remove(slot(name, "stop"));
    // Ignore a request already on disk. The first turn is the next write after watch begins.
    auto seen = std::filesystem::file_time_type::min();
    if (std::filesystem::is_regular_file(request)) seen = std::filesystem::last_write_time(request);
    while (!std::filesystem::exists(slot(name, "stop"))) {
        if (std::filesystem::is_regular_file(request)) {
            const auto stamp = std::filesystem::last_write_time(request);
            if (stamp != seen) {
                seen = stamp;
                const auto text = read_text(request);
                if (!text.empty()) fn(text);
            }
        }
        Sleep(poll_ms);
    }
    std::filesystem::remove(slot(name, "pid"));
    std::filesystem::remove(slot(name, "stop"));
    return 0;
}

} // namespace trident
