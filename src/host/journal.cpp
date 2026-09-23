#include "journal.hpp"
#include <chrono>
#include <ctime>
#include <fstream>
#include <sstream>

namespace trident::host {
static std::string now_string() {
    std::time_t t = std::time(nullptr);
    std::tm tm{};
    localtime_s(&tm, &t);
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &tm);
    return buf;
}

void Journal::load(const std::filesystem::path& path) {
    rows_.clear();
    next_id_ = 1;
    if (!std::filesystem::is_regular_file(path)) return;
    std::ifstream in(path);
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        try {
            auto row = json::parse(line);
            rows_.push_back(row);
        } catch (...) {
            continue;
        }
    }
    if (!rows_.empty()) {
        next_id_ = rows_.back().value("id", 0) + 1;
        for (const auto& row : rows_)
            if (row.value("type", "") == "heard") last_heard_ = row.value("time", 0.0);
    }
}

double Journal::last_heard() const {
    std::lock_guard lock(mutex_);
    return last_heard_;
}

json Journal::add(const std::string& type, const std::string& source, const json& data, bool trigger,
                  const std::filesystem::path& log) {
    json row = {{"id", next_id_},
                {"at", now_string()},
                {"time", std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count()},
                {"type", type},
                {"source", source},
                {"trigger", trigger},
                {"data", data}};
    std::string line = row.dump() + "\n";
    {
        std::ofstream out(log, std::ios::app);
        out << line;
        out.flush();
    }
    std::lock_guard lock(mutex_);
    rows_.push_back(row);
    if (type == "heard") last_heard_ = row["time"].get<double>();
    ++next_id_;
    changed_.notify_all();
    return row;
}

std::vector<json> Journal::recent(int limit) const {
    std::lock_guard lock(mutex_);
    limit = std::max(1, std::min(limit, 500));
    if (static_cast<int>(rows_.size()) <= limit) return rows_;
    return std::vector<json>(rows_.end() - limit, rows_.end());
}

std::vector<json> Journal::after(int id, double timeout_sec, bool stop) {
    using clock = std::chrono::steady_clock;
    auto deadline = clock::now() + std::chrono::duration<double>(timeout_sec);
    std::unique_lock lock(mutex_);
    for (;;) {
        std::vector<json> out;
        for (const auto& row : rows_)
            if (row.value("id", 0) > id) out.push_back(row);
        if (!out.empty() || stop) {
            if (out.size() > 100) out.resize(100);
            return out;
        }
        if (clock::now() >= deadline) return {};
        changed_.wait_until(lock, deadline);
    }
}

void Journal::notify() { changed_.notify_all(); }
}
