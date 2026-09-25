#include "common/config.h"
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <map>
#include <string>
#include <vector>
#include <windows.h>

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

std::string audio_path(const std::string& text) {
    auto path = std::filesystem::u8path(text);
    if (path.is_relative()) path = trident::trident_file().parent_path() / path;
    return trident::path_u8(path);
}

std::wstring nemo_command(const std::filesystem::path& nemo, const std::map<std::string, std::string>& values, const std::wstring& head);

struct Stay {
    HANDLE write = nullptr;
    HANDLE read = nullptr;
    HANDLE nul = nullptr;
    PROCESS_INFORMATION process{};
};

std::string read_line(HANDLE handle) {
    std::string line;
    char byte = 0;
    DWORD got = 0;
    while (ReadFile(handle, &byte, 1, &got, nullptr) && got == 1) {
        if (byte == '\n') break;
        if (byte != '\r') line.push_back(byte);
    }
    return line;
}

bool write_all(HANDLE handle, const std::string& text) {
    DWORD done = 0;
    while (done < text.size()) {
        DWORD wrote = 0;
        if (!WriteFile(handle, text.data() + done, (DWORD)(text.size() - done), &wrote, nullptr) || !wrote) return false;
        done += wrote;
    }
    return true;
}

Stay open_stay(const std::filesystem::path& nemo, const std::map<std::string, std::string>& values) {
    SECURITY_ATTRIBUTES inherit{};
    inherit.nLength = sizeof(inherit);
    inherit.bInheritHandle = TRUE;
    HANDLE in_read = nullptr, in_write = nullptr, out_read = nullptr, out_write = nullptr;
    if (!CreatePipe(&in_read, &in_write, &inherit, 0) || !CreatePipe(&out_read, &out_write, &inherit, 0))
        return {};
    SetHandleInformation(in_write, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(out_read, HANDLE_FLAG_INHERIT, 0);
    HANDLE nul = CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_WRITE, &inherit, OPEN_EXISTING, 0, nullptr);
    auto command = nemo_command(nemo, values, L"--quiet transcribe --stay");
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(0);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = in_read;
    startup.hStdOutput = out_write;
    startup.hStdError = nul;
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(nemo.c_str(), mutable_command.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process)) {
        CloseHandle(in_read);
        CloseHandle(in_write);
        CloseHandle(out_read);
        CloseHandle(out_write);
        if (nul) CloseHandle(nul);
        return {};
    }
    CloseHandle(in_read);
    CloseHandle(out_write);
    Stay stay;
    stay.write = in_write;
    stay.read = out_read;
    stay.nul = nul;
    stay.process = process;
    for (int i = 0; i < 8; ++i) {
        if (read_line(out_read) == "ready") return stay;
        DWORD code = 0;
        if (GetExitCodeProcess(process.hProcess, &code) && code != STILL_ACTIVE) break;
    }
    return {};
}

void close_stay(Stay& stay) {
    if (stay.write) CloseHandle(stay.write);
    if (stay.read) CloseHandle(stay.read);
    if (stay.nul) CloseHandle(stay.nul);
    if (stay.process.hProcess) {
        WaitForSingleObject(stay.process.hProcess, 15000);
        CloseHandle(stay.process.hThread);
        CloseHandle(stay.process.hProcess);
    }
    stay = {};
}

std::string stay_ask(Stay& stay, const std::string& audio) {
    if (!write_all(stay.write, audio + "\n")) return {};
    return read_line(stay.read);
}

int transcribe(const std::filesystem::path& nemo, const std::map<std::string, std::string>& values, const std::string& audio) {
    const auto response = trident::cfg_path(values, "ear.response-file");
    auto command = nemo_command(nemo, values, quote(L"transcribe") + L" " + quote(widen(audio))
        + L" " + quote(L"--output") + L" " + quote(response.wstring()));
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(0);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(nemo.c_str(), mutable_command.data(), nullptr, nullptr, TRUE, 0, nullptr, nullptr, &startup, &process))
        return 1;
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return (int)code;
}

std::wstring nemo_command(const std::filesystem::path& nemo, const std::map<std::string, std::string>& values, const std::wstring& head) {
    std::wstring command = quote(nemo.wstring()) + L" " + head
        + L" " + quote(L"--model") + L" " + quote(trident::cfg_path(values, "ear.model").wstring());
    const std::string prefix = "ear.opt.";
    for (const auto& [key, value] : values) {
        if (key.compare(0, prefix.size(), prefix) != 0) continue;
        if (value.empty() || value == "off") continue;
        command += L" " + quote(widen(key.substr(prefix.size())));
        if (value != "on") command += L" " + quote(widen(value));
    }
    return command;
}

std::string live_text(const std::string& line) {
    const auto mark = line.rfind("] ");
    const auto tag = line.find("[live final @");
    if (tag == std::string::npos || mark == std::string::npos || mark < tag) return {};
    auto text = line.substr(mark + 2);
    while (!text.empty() && (text.back() == '\r' || text.back() == ' ')) text.pop_back();
    return text;
}

int live(const std::filesystem::path& nemo, const std::map<std::string, std::string>& values) {
    SECURITY_ATTRIBUTES inherit{};
    inherit.nLength = sizeof(inherit);
    inherit.bInheritHandle = TRUE;
    HANDLE read = nullptr, write = nullptr;
    if (!CreatePipe(&read, &write, &inherit, 0)) return 1;
    SetHandleInformation(read, HANDLE_FLAG_INHERIT, 0);
    auto command = nemo_command(nemo, values, L"transcribe --live --force");
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(0);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = write;
    startup.hStdError = write;
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(nemo.c_str(), mutable_command.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process)) {
        CloseHandle(read);
        CloseHandle(write);
        return 1;
    }
    CloseHandle(write);
    trident::write_pid("ear");
    std::filesystem::remove(trident::slot("ear", "stop"));
    const auto response = trident::cfg_path(values, "ear.response-file");
    std::string pending;
    while (!std::filesystem::exists(trident::slot("ear", "stop"))) {
        DWORD ready = 0;
        if (!PeekNamedPipe(read, nullptr, 0, nullptr, &ready, nullptr)) break;
        if (ready) {
            char buf[4096];
            DWORD got = 0;
            if (!ReadFile(read, buf, sizeof(buf), &got, nullptr) || !got) break;
            pending.append(buf, buf + got);
            for (;;) {
                const auto cut = pending.find('\n');
                if (cut == std::string::npos) break;
                const auto line = pending.substr(0, cut);
                pending.erase(0, cut + 1);
                const auto text = live_text(line);
                if (!text.empty()) std::ofstream(response, std::ios::binary | std::ios::trunc) << text;
            }
        } else {
            Sleep(trident::cfg_int(values, "ear.poll-ms"));
        }
        DWORD code = 0;
        if (GetExitCodeProcess(process.hProcess, &code) && code != STILL_ACTIVE) break;
    }
    TerminateProcess(process.hProcess, 0);
    WaitForSingleObject(process.hProcess, 5000);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    CloseHandle(read);
    std::filesystem::remove(trident::slot("ear", "pid"));
    std::filesystem::remove(trident::slot("ear", "stop"));
    return 0;
}

} // namespace

int main(int, char**) {
    const auto values = trident::load_trident();
    if (trident::cfg_on(values, "ear.unload")) return trident::unload_named("ear");
    if (trident::resident("ear")) return 0;
    const auto nemo = trident::exe_dir() / "nemo-speech.exe";
    if (!std::filesystem::is_regular_file(nemo)) {
        std::fprintf(stderr, "ear error: missing %s\n", trident::path_u8(nemo).c_str());
        return 1;
    }
    if (trident::cfg_on(values, "ear.live")) return live(nemo, values);
    const auto request = trident::cfg_path(values, "ear.prompt-file");
    const int poll_ms = trident::cfg_int(values, "ear.poll-ms");
    if (trident::cfg_on(values, "ear.persist")) {
        const auto response = trident::cfg_path(values, "ear.response-file");
        Stay stay = open_stay(nemo, values);
        if (!stay.process.hProcess) {
            std::fprintf(stderr, "ear error: recognizer did not stay warm\n");
            close_stay(stay);
            return 1;
        }
        const int code = trident::watch("ear", request, poll_ms, [&](const std::string& audio) {
            const auto text = stay_ask(stay, audio_path(audio));
            std::ofstream(response, std::ios::binary | std::ios::trunc) << text;
        });
        close_stay(stay);
        return code;
    }
    const auto audio = trident::read_text(request);
    if (audio.empty()) {
        std::fprintf(stderr, "ear.prompt-file is empty\n");
        return 2;
    }
    return transcribe(nemo, values, audio_path(audio));
}
