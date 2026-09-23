#include "supervisor.hpp"
#include "paths.hpp"
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <vector>

namespace trident::host {
static std::wstring quote(const std::wstring& s) { return L"\"" + s + L"\""; }

static bool spawn(const std::wstring& cmd, PROCESS_INFORMATION& pi) {
    STARTUPINFOW si{};
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    std::vector<wchar_t> buf(cmd.begin(), cmd.end());
    buf.push_back(L'\0');
    return CreateProcessW(nullptr, buf.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi);
}

Supervisor::Supervisor(Bus& bus, std::string variant, bool inbox_only)
    : bus_(bus), variant_(std::move(variant)), inbox_only_(inbox_only) {}

Supervisor::~Supervisor() { terminate_all(workers_); }

void Supervisor::terminate_all(std::vector<std::pair<std::string, PROCESS_INFORMATION>>& workers) {
    for (auto& [name, pi] : workers) {
        if (!pi.hProcess) continue;
        TerminateProcess(pi.hProcess, 1);
        WaitForSingleObject(pi.hProcess, 5000);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    }
    workers.clear();
}

bool Supervisor::speech_pending() {
    auto folder = work();
    if (!std::filesystem::is_directory(folder)) return false;
    for (const auto& entry : std::filesystem::directory_iterator(folder))
        if (entry.path().filename().string().rfind("speech-", 0) == 0) return true;
    return false;
}

void Supervisor::drain_speech() {
    HANDLE mouth = nullptr;
    for (auto& [name, pi] : workers_)
        if (name == "mouth") mouth = pi.hProcess;
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(600);
    while (std::chrono::steady_clock::now() < deadline) {
        if (!speech_pending()) return;
        if (mouth && WaitForSingleObject(mouth, 0) == WAIT_OBJECT_0) {
            fprintf(stderr, "mouth exited before speech drained\n");
            return;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
    }
    fprintf(stderr, "speech drain timeout\n");
}

void Supervisor::start(int port) {
    auto py = venv_python();
    auto repo = trident::host::root();
    std::ostringstream url;
    url << "http://127.0.0.1:" << port;
    std::wstring wurl(url.str().begin(), url.str().end());

    PROCESS_INFORMATION pi{};
    std::wstring brain_cmd =
        quote(py.wstring()) + L" " + quote((repo / "brain.py").wstring()) + L" " + wurl;
    if (!spawn(brain_cmd, pi)) throw std::runtime_error("brain spawn failed");
    workers_.emplace_back("brain", pi);

    auto mouth_exe = exe_dir() / "trident-mouth.exe";
    std::wstring mouth_cmd = quote(mouth_exe.wstring()) + L" " + std::wstring(variant_.begin(), variant_.end()) + L" " + wurl;
    ZeroMemory(&pi, sizeof(pi));
    if (!spawn(mouth_cmd, pi)) throw std::runtime_error("mouth spawn failed");
    workers_.emplace_back("mouth", pi);

    std::wstring ear_cmd = quote(py.wstring()) + L" " + quote((repo / "ear.py").wstring()) + L" " + wurl;
    if (inbox_only_) ear_cmd += L" --inbox";
    ZeroMemory(&pi, sizeof(pi));
    if (!spawn(ear_cmd, pi)) throw std::runtime_error("ear spawn failed");
    workers_.emplace_back("ear", pi);

    auto ready = work() / "ready";
    if (std::filesystem::is_directory(ready))
        for (const auto& entry : std::filesystem::directory_iterator(ready))
            if (entry.is_regular_file()) std::filesystem::remove(entry.path());
}

void Supervisor::run() {
    auto ready = work() / "ready";
    while (!bus_.stopped()) {
        bool all = true;
        for (const auto& [name, _] : workers_) {
            if (!std::filesystem::is_regular_file(ready / name)) {
                all = false;
                break;
            }
        }
        if (all) break;
        for (auto& [name, pi] : workers_) {
            if (WaitForSingleObject(pi.hProcess, 0) == WAIT_OBJECT_0) {
                DWORD code = 0;
                GetExitCodeProcess(pi.hProcess, &code);
                throw std::runtime_error(name + " exited " + std::to_string(code));
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    if (!bus_.stopped()) fprintf(stderr, "jarvis ready\n");

    while (!bus_.stopped()) {
        for (auto& [name, pi] : workers_) {
            if (WaitForSingleObject(pi.hProcess, 0) != WAIT_OBJECT_0) continue;
            if (bus_.stopped()) break;
            DWORD code = 0;
            GetExitCodeProcess(pi.hProcess, &code);
            throw std::runtime_error(name + " exited " + std::to_string(code));
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
    }
    drain_speech();
}
}
