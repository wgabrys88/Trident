#include "common/config.h"
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

void bare(std::wstring& command, const std::map<std::string, std::string>& values, const char* key, const wchar_t* flag) {
    if (!trident::cfg_on(values, key)) return;
    command += L" ";
    command += flag;
}

std::wstring nemo_command(const std::filesystem::path& nemo, const std::map<std::string, std::string>& values) {
    std::wstring command = quote(nemo.wstring()) + L" transcribe " + quote(trident::cfg_path(values, "ear.input").wstring())
        + L" --model " + quote(trident::cfg_path(values, "ear.model").wstring())
        + L" --device " + quote(widen(trident::need(values, "ear.device")))
        + L" --format " + quote(widen(trident::need(values, "ear.format")))
        + L" --endpointing=" + widen(trident::need(values, "ear.endpointing"))
        + L" --stop-history-eou-ms " + quote(widen(trident::need(values, "ear.stop-history-eou-ms")));
    const auto language = trident::cfg_key(values, "ear.language");
    if (!language.empty()) command += L" --language " + quote(widen(language));
    bare(command, values, "ear.stream", L"--stream");
    bare(command, values, "ear.verbatim", L"--verbatim");
    bare(command, values, "ear.no-punctuation", L"--no-punctuation");
    return command;
}

std::string capture(HANDLE handle) {
    std::string out;
    char buf[4096];
    DWORD got = 0;
    while (ReadFile(handle, buf, sizeof(buf), &got, nullptr) && got) out.append(buf, buf + got);
    return out;
}

} // namespace

int main(int argc, char** argv) {
    const auto values = trident::load_settings(argc, argv);
    trident::cfg_on(values, "ear.endpointing");
    const auto nemo = trident::exe_dir() / "nemo-speech.exe";
    if (!std::filesystem::is_regular_file(nemo)) trident::fail("missing " + trident::path_u8(nemo));
    SECURITY_ATTRIBUTES inherit{};
    inherit.nLength = sizeof(inherit);
    inherit.bInheritHandle = TRUE;
    HANDLE read = nullptr, write = nullptr;
    if (!CreatePipe(&read, &write, &inherit, 0)) trident::fail("recognizer pipe failed");
    SetHandleInformation(read, HANDLE_FLAG_INHERIT, 0);
    auto command = nemo_command(nemo, values);
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(0);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = write;
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(nemo.c_str(), mutable_command.data(), nullptr, nullptr, TRUE, 0, nullptr, nullptr, &startup, &process)) {
        CloseHandle(read);
        CloseHandle(write);
        trident::fail("recognizer did not start");
    }
    CloseHandle(write);
    const auto text = capture(read);
    CloseHandle(read);
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    if (code != 0) return (int)code;
    trident::write_output("ear", text);
    return 0;
}
