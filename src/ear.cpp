#include "common/config.h"
#include <cstdio>
#include <filesystem>
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

int transcribe(const std::filesystem::path& nemo, const std::map<std::string, std::string>& values, const std::string& audio) {
    const auto response = trident::cfg_path(values, "ear.response-file");
    const auto model = trident::cfg_path(values, "ear.model");
    std::wstring command = quote(nemo.wstring()) + L" " + quote(L"transcribe") + L" " + quote(widen(audio))
        + L" " + quote(L"--model") + L" " + quote(model.wstring())
        + L" " + quote(L"--output") + L" " + quote(response.wstring());
    const std::string prefix = "ear.opt.";
    for (const auto& [key, value] : values) {
        if (key.compare(0, prefix.size(), prefix) != 0) continue;
        if (value.empty() || value == "off") continue;
        command += L" " + quote(widen(key.substr(prefix.size())));
        if (value != "on") command += L" " + quote(widen(value));
    }
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
    const auto request = trident::cfg_path(values, "ear.prompt-file");
    const int poll_ms = trident::cfg_int(values, "ear.poll-ms");
    if (trident::cfg_on(values, "ear.persist"))
        return trident::watch("ear", request, poll_ms, [&](const std::string& audio) {
            transcribe(nemo, values, audio_path(audio));
        });
    const auto audio = trident::read_text(request);
    if (audio.empty()) {
        std::fprintf(stderr, "ear.prompt-file is empty\n");
        return 2;
    }
    return transcribe(nemo, values, audio_path(audio));
}
