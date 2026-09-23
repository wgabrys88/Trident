#pragma once
#include <condition_variable>
#include <filesystem>
#include <mutex>
#include <nlohmann/json.hpp>
#include <vector>

namespace trident::host {
using json = nlohmann::json;

class Journal {
    mutable std::mutex mutex_;
    std::condition_variable changed_;
    std::vector<json> rows_;
    int next_id_ = 1;
    double last_heard_ = 0;

public:
    void load(const std::filesystem::path& path);
    json add(const std::string& type, const std::string& source, const json& data, bool trigger, const std::filesystem::path& log);
    std::vector<json> after(int id, double timeout_sec, bool stop);
    std::vector<json> recent(int limit) const;
    double last_heard() const;
    void notify();
    int next_id() const { return next_id_; }
};
}
