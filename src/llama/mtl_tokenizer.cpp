#include "mtl_tokenizer.h"
#include <cstring>

namespace trident::llama {
std::wstring MtlTokenizer::wide(const std::string& text) {
    int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), int(text.size()), nullptr, 0);
    std::wstring result(count, L'\0');
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), int(text.size()), result.data(), count) != count)
        throw std::runtime_error("UTF-8 conversion failed");
    return result;
}
std::wstring MtlTokenizer::quote(const std::wstring& argument) {
    std::wstring result = L"\"";
    size_t slashes = 0;
    for (wchar_t c : argument) {
        if (c == L'\\') { ++slashes; continue; }
        result.append(c == L'"' ? slashes * 2 + 1 : slashes, L'\\');
        result += c; slashes = 0;
    }
    result.append(slashes * 2, L'\\'); result += L'"';
    return result;
}
MtlTokenizer::MtlTokenizer(const TokenizerPaths& paths) {
    SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
    HANDLE child_read, parent_write, parent_read, child_write;
    if (!CreatePipe(&child_read, &parent_write, &security, 0)) throw std::runtime_error("Tokenizer input pipe creation failed");
    Handle stdin_child(child_read); input_.reset(parent_write);
    if (!CreatePipe(&parent_read, &child_write, &security, 0)) throw std::runtime_error("Tokenizer output pipe creation failed");
    Handle stdout_child(child_write); output_.reset(parent_read);
    if (!SetHandleInformation(input_.get(), HANDLE_FLAG_INHERIT, 0) || !SetHandleInformation(output_.get(), HANDLE_FLAG_INHERIT, 0))
        throw std::runtime_error("Tokenizer pipe inheritance failed");
    std::vector<std::string> arguments = {paths.python, paths.script, "--source", paths.source, "--tts-source", paths.tts_source,
        "--tokenizer", paths.tokenizer_json, "--cangjie", paths.cangjie_json, "--dicta-model", paths.dicta_model, "--language", paths.language_id};
    std::wstring command;
    for (const auto& argument : arguments) { if (!command.empty()) command += L' '; command += quote(wide(argument)); }
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup); startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = stdin_child.get(); startup.hStdOutput = stdout_child.get(); startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(nullptr, command.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process))
        throw std::runtime_error("Tokenizer process creation failed");
    Handle thread(process.hThread); process_.reset(process.hProcess);
    stdin_child.reset(); stdout_child.reset(); request('R', "");
}
MtlTokenizer::~MtlTokenizer() {
    input_.reset();
    if (WaitForSingleObject(process_.get(), 3000) == WAIT_TIMEOUT) {
        TerminateProcess(process_.get(), 1); WaitForSingleObject(process_.get(), 3000);
    }
}
std::vector<uint8_t> MtlTokenizer::request(char mode, const std::string& text) {
    uint32_t size = uint32_t(text.size());
    Pipe::write(input_.get(), &mode, 1); Pipe::write(input_.get(), &size, sizeof(size));
    Pipe::write(input_.get(), text.data(), size);
    uint32_t status, length;
    Pipe::read(output_.get(), &status, sizeof(status)); Pipe::read(output_.get(), &length, sizeof(length));
    std::vector<uint8_t> payload(length);
    Pipe::read(output_.get(), payload.data(), length);
    if (status != 0) throw std::runtime_error("Tokenizer request failed");
    return payload;
}
std::string MtlTokenizer::punctuation(const std::string& text) {
    auto payload = request('P', text); return std::string(payload.begin(), payload.end());
}
std::vector<int32_t> MtlTokenizer::tokenize(const std::string& text) {
    auto payload = request('T', text);
    uint32_t count; std::memcpy(&count, payload.data(), sizeof(count));
    std::vector<int32_t> ids(count);
    std::memcpy(ids.data(), payload.data() + sizeof(count), count * sizeof(int32_t)); return ids;
}
}
