#pragma once
#include "audio.h"
#include "../engine.h"
#include <map>
#include <stdexcept>
#include <string>
#include <windows.h>

namespace trident {
class Handle {
    HANDLE value_;
public:
    explicit Handle(HANDLE value = nullptr) : value_(value) {}
    ~Handle() { if (value_ && value_ != INVALID_HANDLE_VALUE) CloseHandle(value_); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    HANDLE get() const { return value_; }
    void reset(HANDLE value = nullptr) { if (value_ && value_ != INVALID_HANDLE_VALUE) CloseHandle(value_); value_ = value; }
};
class Pipe {
public:
    static void read(HANDLE handle, void* data, DWORD bytes) {
        auto* position = static_cast<char*>(data);
        while (bytes) {
            DWORD count;
            if (!ReadFile(handle, position, bytes, &count, nullptr) || !count) throw std::runtime_error("Pipe read failed");
            position += count; bytes -= count;
        }
    }
    static void write(HANDLE handle, const void* data, DWORD bytes) {
        auto* position = static_cast<const char*>(data);
        while (bytes) {
            DWORD count;
            if (!WriteFile(handle, position, bytes, &count, nullptr) || !count) throw std::runtime_error("Pipe write failed");
            position += count; bytes -= count;
        }
    }
    static std::string line(HANDLE handle) {
        std::string result;
        for (;;) { char c; read(handle, &c, 1); if (c == '\n') return result; result += c; }
    }
};
class Flags {
    std::map<std::string, std::string> values_;
public:
    Flags(int argc, char** argv) {
        if (argc < 4 || (argc - 4) % 2) throw std::runtime_error("Expected T3.gguf S3.gguf pipe and flag/value pairs");
        for (int i = 4; i < argc; i += 2)
            if (!values_.emplace(argv[i], argv[i + 1]).second) throw std::runtime_error("Duplicate flag");
    }
    std::string string(const char* name) {
        auto value = values_.at(name); values_.erase(name); return value;
    }
    int integer(const char* name) { return std::stoi(string(name)); }
    float real(const char* name) { return std::stof(string(name)); }
    void finish() const { if (!values_.empty()) throw std::runtime_error("Unknown flag: " + values_.begin()->first); }
};
class PipeServer {
    Handle pipe_;
public:
    explicit PipeServer(const char* name)
        : pipe_(CreateNamedPipeA(name, PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1, 4096, 4096, 0, nullptr)) {
        if (pipe_.get() == INVALID_HANDLE_VALUE) throw std::runtime_error("Pipe creation failed");
    }
    void serve(Synth& engine) {
        for (;;) {
            if (!ConnectNamedPipe(pipe_.get(), nullptr) && GetLastError() != ERROR_PIPE_CONNECTED) throw std::runtime_error("Pipe connection failed");
            auto path = Pipe::line(pipe_.get());
            std::string text(std::stoul(Pipe::line(pipe_.get())), '\0');
            Pipe::read(pipe_.get(), text.data(), DWORD(text.size()));
            try {
                Audio(engine.synthesize(text), 24000).write(path);
                Pipe::write(pipe_.get(), "ok\n", 3);
            } catch (const std::exception& e) {
                std::string reply = std::string("error ") + e.what() + "\n";
                Pipe::write(pipe_.get(), reply.data(), DWORD(reply.size()));
            }
            if (!FlushFileBuffers(pipe_.get()) || !DisconnectNamedPipe(pipe_.get())) throw std::runtime_error("Pipe completion failed");
        }
    }
};
}
