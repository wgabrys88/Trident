#pragma once
#include <filesystem>
#include <windows.h>

namespace trident::host {
inline std::filesystem::path exe_dir() {
    wchar_t buf[MAX_PATH];
    DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    return std::filesystem::path(buf, buf + n).parent_path();
}
inline std::filesystem::path root() { return std::filesystem::weakly_canonical(exe_dir() / ".." / ".."); }
inline std::filesystem::path work() { return root() / "workspace"; }
inline std::filesystem::path models() { return root() / "models"; }
inline std::filesystem::path venv_python() { return root() / ".venv" / "Scripts" / "python.exe"; }
}
