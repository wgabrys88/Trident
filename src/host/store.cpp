#include "store.hpp"
#include <fstream>

namespace trident::host {
Store::Store(std::filesystem::path root) : root_(std::move(root)), work_(root_ / "workspace"), done_(work_ / "done") {}

void Store::ensure_bus() const {
    std::filesystem::create_directories(work_);
    std::filesystem::create_directories(done_);
    for (const char* name : {"inbox", "ready"})
        std::filesystem::create_directories(work_ / name);
    for (const char* kind : {"inbox", "transcription", "speech", "wav", "job", "wake", "ready"})
        std::filesystem::create_directories(done_ / kind);
    auto memory = work_ / "memory.md";
    if (!std::filesystem::is_regular_file(memory)) atomic_text(memory, "");
}

void Store::atomic_text(const std::filesystem::path& path, const std::string& text) {
    std::filesystem::create_directories(path.parent_path());
    auto tmp = path.parent_path() / (path.filename().string() + ".tmp");
    std::ofstream out(tmp, std::ios::binary);
    out << text;
    out.close();
    std::filesystem::rename(tmp, path);
}

std::filesystem::path Store::retire(const std::filesystem::path& path, const std::string& kind) const {
    auto folder = done_ / kind;
    std::filesystem::create_directories(folder);
    auto dest = folder / path.filename();
    int n = 1;
    while (std::filesystem::exists(dest))
        dest = folder / (path.stem().string() + "-" + std::to_string(n++) + path.extension().string());
    std::filesystem::rename(path, dest);
    return dest;
}

std::filesystem::path Store::numbered(const std::string& prefix, const std::string& suffix) const {
    for (int n = 1;; ++n) {
        auto path = work_ / (prefix + "-" + std::to_string(n) + suffix);
        if (!std::filesystem::exists(path) && !std::filesystem::exists(done_ / prefix / path.filename())) return path;
    }
}

std::filesystem::path Store::archive_rel(const std::string& rel, const std::string& kind) const {
    if (rel.empty() || kind.empty()) throw std::runtime_error("archive path and kind required");
    auto root = std::filesystem::weakly_canonical(root_);
    auto path = std::filesystem::weakly_canonical(root_ / rel);
    auto rel_to = std::filesystem::relative(path, root);
    auto rel_s = rel_to.generic_string();
    if (rel_s.empty() || rel_s.rfind("..", 0) == 0) throw std::runtime_error("archive path outside root");
    if (!std::filesystem::is_regular_file(path)) throw std::runtime_error("missing " + rel);
    return retire(path, kind);
}

int Store::speech_index(const std::string& name) {
    auto stem = std::filesystem::path(name).stem().string();
    auto pos = stem.rfind('-');
    if (pos == std::string::npos) return 0;
    try {
        return std::stoi(stem.substr(pos + 1));
    } catch (...) {
        return 0;
    }
}
}
