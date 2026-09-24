#pragma once

#include <filesystem>
#include <fstream>
#include <map>
#include <string>
#include <windows.h>

namespace trident {

inline std::filesystem::path trident_file() {
    wchar_t buf[MAX_PATH];
    const DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    auto dir = std::filesystem::path(buf, buf + n).parent_path();
    for (;;) {
        auto candidate = dir / "trident.txt";
        if (std::filesystem::is_regular_file(candidate)) return candidate;
        auto parent = dir.parent_path();
        if (parent == dir) return {};
        dir = parent;
    }
}

inline std::map<std::string, std::string> load_trident() {
    std::map<std::string, std::string> out;
    std::ifstream in(trident_file());
    std::string line;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
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

inline std::string cfg(const std::map<std::string, std::string> & values, const std::string & key, const std::string & fallback = {}) {
    const auto it = values.find(key);
    return it == values.end() ? fallback : it->second;
}

inline std::filesystem::path cfg_path(const std::map<std::string, std::string> & values, const std::string & key) {
    return trident_file().parent_path() / cfg(values, key);
}

inline std::filesystem::path slot(const std::string & name, const char * kind) {
    return trident_file().parent_path() / (name + "." + kind);
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
    for (int i = 0; i < 50 && pid_alive(pid); ++i) Sleep(100);
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

} // namespace trident
