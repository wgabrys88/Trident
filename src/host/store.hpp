#pragma once
#include <filesystem>
#include <string>

namespace trident::host {
class Store {
    std::filesystem::path root_, work_, done_;

public:
    explicit Store(std::filesystem::path root);
    void ensure_bus() const;
    static void atomic_text(const std::filesystem::path& path, const std::string& text);
    std::filesystem::path retire(const std::filesystem::path& path, const std::string& kind) const;
    std::filesystem::path numbered(const std::string& prefix, const std::string& suffix = ".txt") const;
    std::filesystem::path archive_rel(const std::string& rel, const std::string& kind) const;
    static int speech_index(const std::string& name);
    const std::filesystem::path& root() const { return root_; }
    const std::filesystem::path& work() const { return work_; }
};
}
