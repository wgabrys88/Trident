#include "bus.hpp"
#include "paths.hpp"
#include <httplib.h>
#include <algorithm>
#include <chrono>
#include <ctime>
#include <fstream>
#include <sstream>

namespace trident::host {
static std::string rel(const std::filesystem::path& root, const std::filesystem::path& path) {
    return std::filesystem::relative(path, root).generic_string();
}

Bus::Bus(std::string variant, int idle_seconds, int vulkan_device)
    : store_(root()),
      events_log_(store_.work() / "events.jsonl"),
      variant_(std::move(variant)),
      idle_seconds_(idle_seconds),
      vulkan_device_(vulkan_device) {
    store_.ensure_bus();
    journal_.load(events_log_);
    last_clock_ = journal_.last_heard();
}

Bus::~Bus() {
    stop_ = true;
    journal_.notify();
    speech_changed_.notify_all();
    if (timer_.joinable()) timer_.join();
}

json Bus::next_speech(double timeout_sec) {
    using clock = std::chrono::steady_clock;
    auto deadline = clock::now() + std::chrono::duration<double>(timeout_sec);
    std::unique_lock lock(speech_mutex_);
    for (;;) {
        std::vector<std::filesystem::path> files;
        for (const auto& entry : std::filesystem::directory_iterator(store_.work())) {
            if (entry.path().filename().string().rfind("speech-", 0) == 0) files.push_back(entry.path());
        }
        std::sort(files.begin(), files.end(), [](const auto& a, const auto& b) {
            return Store::speech_index(a.filename().string()) < Store::speech_index(b.filename().string());
        });
        if (!files.empty()) {
            std::ifstream in(files.front());
            std::string language, line, text;
            std::getline(in, language);
            while (std::getline(in, line)) {
                if (!text.empty()) text += '\n';
                text += line;
            }
            return json{{"name", files.front().filename().string()},
                        {"language", language},
                        {"text", text}};
        }
        if (stop_) return nullptr;
        if (clock::now() >= deadline) return nullptr;
        speech_changed_.wait_until(lock, deadline);
    }
}

void Bus::timer_loop() {
    while (!stop_) {
        double now = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
        for (const auto& entry : std::filesystem::directory_iterator(store_.work())) {
            auto path = entry.path();
            if (path.filename().string().rfind("wake-", 0) != 0) continue;
            try {
                std::ifstream in(path);
                auto payload = json::parse(in);
                if (now >= payload.value("due", 0.0)) {
                    json data = {{"reason", payload.value("reason", "")},
                                 {"artifact", rel(store_.root(), path)}};
                    journal_.add("wake", "clock", data, true, events_log_);
                    store_.retire(path, "wake");
                }
            } catch (const std::exception& err) {
                fprintf(stderr, "wake skipped %s %s\n", path.filename().string().c_str(), err.what());
            }
        }
        double heard = journal_.last_heard();
        if (now - heard >= idle_seconds_ && now - last_clock_ >= idle_seconds_) {
            last_clock_ = now;
            journal_.add("clock", "clock", json{{"seconds_since_heard", static_cast<int>(now - heard)}}, true, events_log_);
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
    }
}

void Bus::run(int port) {
    timer_ = std::thread([this] { timer_loop(); });
    httplib::Server http;
    http.set_address_family(AF_INET);
    auto& root = store_.root();
    auto& store = store_;
    auto& journal = journal_;
    auto events_log = events_log_;
    auto variant = variant_;
    auto idle = idle_seconds_;
    auto vulkan = vulkan_device_;
    auto& stop = stop_;
    auto& speech_cv = speech_changed_;
    auto& speech_mu = speech_mutex_;

    http.Get("/health", [&](const httplib::Request&, httplib::Response& res) {
        res.set_content(json{{"ok", true}, {"stop", stop.load()}}.dump(), "application/json");
    });
    http.Get("/config", [&](const httplib::Request&, httplib::Response& res) {
        res.set_content(json{{"variant", variant}, {"vulkan_device", vulkan}, {"idle_seconds", idle}}.dump(), "application/json");
    });
    http.Get("/memory", [&](const httplib::Request&, httplib::Response& res) {
        std::ifstream in(store.work() / "memory.md");
        std::ostringstream text;
        text << in.rdbuf();
        res.set_content(json{{"text", text.str()}}.dump(), "application/json");
    });
    http.Get(R"(/events/recent)", [&](const httplib::Request& req, httplib::Response& res) {
        int limit = 80;
        if (req.has_param("limit")) limit = std::stoi(req.get_param_value("limit"));
        res.set_content(json{{"events", journal.recent(limit)}}.dump(), "application/json");
    });
    http.Get("/events", [&](const httplib::Request& req, httplib::Response& res) {
        int after = req.has_param("after") ? std::stoi(req.get_param_value("after")) : 0;
        double timeout = req.has_param("timeout") ? std::stod(req.get_param_value("timeout")) : 30.0;
        timeout = std::min(60.0, std::max(0.0, timeout));
        auto rows = journal.after(after, timeout, stop.load());
        res.set_content(json{{"events", rows}}.dump(), "application/json");
    });
    http.Get("/speech/next", [&](const httplib::Request& req, httplib::Response& res) {
        double timeout = req.has_param("timeout") ? std::stod(req.get_param_value("timeout")) : 30.0;
        timeout = std::min(60.0, std::max(0.0, timeout));
        res.set_content(json{{"speech", next_speech(timeout)}}.dump(), "application/json");
    });

    auto fail = [](httplib::Response& res, const std::exception& err) {
        res.status = 500;
        res.set_content(json{{"error", err.what()}}.dump(), "application/json");
    };

    http.Post("/ready", [&](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);
            auto name = body.at("name").get<std::string>();
            auto stamp = std::to_string(std::time(nullptr)) + "\n";
            Store::atomic_text(store.work() / "ready" / name, stamp);
            auto row = journal.add("ready", name, json::object(), false, events_log);
            res.set_content(row.dump(), "application/json");
        } catch (const std::exception& err) { fail(res, err); }
    });
    http.Post("/heard", [&](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);
            std::string text = body.value("text", "");
            if (text.empty()) {
                res.set_content(json{{"ignored", true}}.dump(), "application/json");
                return;
            }
            std::string language = body.value("language", "");
            std::string times = body.value("timestamps", "");
            auto path = store.numbered("transcription");
            std::string first = (language.empty() ? "" : "<" + language + "> ") + text;
            std::string payload = first + "\n" + (times.empty() ? "" : times + "\n");
            Store::atomic_text(path, payload);
            auto archived = store.retire(path, "transcription");
            json data = {{"text", text},
                         {"language", language},
                         {"timestamps", times},
                         {"artifact", rel(root, archived)}};
            auto row = journal.add("heard", "ear", data, true, events_log);
            res.set_content(row.dump(), "application/json");
        } catch (const std::exception& err) { fail(res, err); }
    });
    http.Post("/event", [&](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);
            auto row = journal.add(body.at("type").get<std::string>(), body.value("source", "unknown"),
                                   body.value("data", json::object()), body.value("trigger", false), events_log);
            res.set_content(row.dump(), "application/json");
        } catch (const std::exception& err) { fail(res, err); }
    });
    http.Post("/archive", [&](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);
            auto archived = store.archive_rel(body.at("path").get<std::string>(), body.at("kind").get<std::string>());
            res.set_content(json{{"artifact", rel(root, archived)}}.dump(), "application/json");
        } catch (const std::exception& err) { fail(res, err); }
    });
    http.Post("/speech", [&](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);
            std::string text = body.value("text", "");
            if (text.empty()) throw std::runtime_error("empty speech");
            std::string language = body.value("language", "en");
            if (language.empty()) language = "en";
            auto path = store.numbered("speech");
            Store::atomic_text(path, language + "\n" + text + "\n");
            json data = {{"text", text}, {"language", language}, {"artifact", rel(root, path)}};
            auto row = journal.add("speech_queued", "brain", data, false, events_log);
            speech_cv.notify_all();
            res.set_content(json{{"event", row}, {"name", path.filename().string()}}.dump(), "application/json");
        } catch (const std::exception& err) { fail(res, err); }
    });
    http.Post("/speech/done", [&](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);
            std::string name = std::filesystem::path(body.at("name").get<std::string>()).filename().string();
            auto path = store.work() / name;
            std::filesystem::path archived;
            if (std::filesystem::is_regular_file(path))
                archived = store.retire(path, "speech");
            else {
                auto candidate = store.work() / "done" / "speech" / name; // workspace/done/speech
                if (!std::filesystem::is_regular_file(candidate))
                    throw std::runtime_error("missing speech artifact " + name);
                archived = candidate;
            }
            std::string wav_archived;
            if (body.contains("wav") && body["wav"].is_string()) {
                std::string wav_rel = body["wav"].get<std::string>();
                if (!wav_rel.empty()) {
                    auto wav_path = std::filesystem::weakly_canonical(root / wav_rel);
                    if (std::filesystem::is_regular_file(wav_path))
                        wav_archived = rel(root, store.retire(wav_path, "wav"));
                    else
                        wav_archived = wav_rel;
                }
            }
            json data = {{"speech", rel(root, archived)}, {"wav", wav_archived}};
            auto row = journal.add("speech_done", "mouth", data, false, events_log);
            speech_cv.notify_all();
            res.set_content(row.dump(), "application/json");
        } catch (const std::exception& err) { fail(res, err); }
    });
    http.Post("/memory", [&](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);
            std::string text = body.value("text", "");
            if (text.empty()) throw std::runtime_error("empty memory");
            auto mem_path = store.work() / "memory.md";
            std::ifstream in(mem_path);
            std::ostringstream current;
            current << in.rdbuf();
            std::string merged = current.str();
            if (!merged.empty() && merged.find_last_not_of(" \t\r\n") != std::string::npos) merged += "\n";
            merged += text + "\n";
            Store::atomic_text(mem_path, merged);
            auto row = journal.add("memory", "brain", json{{"text", text}}, false, events_log);
            res.set_content(row.dump(), "application/json");
        } catch (const std::exception& err) { fail(res, err); }
    });
    http.Post("/wake", [&](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);
            double seconds = body.value("seconds", 0.0);
            if (seconds <= 0) throw std::runtime_error("wake seconds");
            double now = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
            json payload = {{"due", now + seconds},
                            {"seconds", seconds},
                            {"reason", body.value("reason", "")}};
            auto path = store.numbered("wake", ".json");
            Store::atomic_text(path, payload.dump() + "\n");
            json data = payload;
            data["artifact"] = rel(root, path);
            auto row = journal.add("wake_scheduled", "brain", data, false, events_log);
            res.set_content(row.dump(), "application/json");
        } catch (const std::exception& err) { fail(res, err); }
    });
    http.Post("/stop", [&](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);
            auto row = journal.add("stop", body.value("source", "brain"), json::object(), false, events_log);
            stop = true;
            journal.notify();
            speech_cv.notify_all();
            res.set_content(row.dump(), "application/json");
        } catch (const std::exception& err) { fail(res, err); }
    });

    journal.add("start", "trident", json{{"variant", variant}}, true, events_log);
    fprintf(stderr, "trident http://127.0.0.1:%d\n", port);
    server_ = &http;
    if (!http.bind_to_port("127.0.0.1", port)) throw std::runtime_error("bind failed");
    http.listen_after_bind();
    server_ = nullptr;
}

void Bus::set_stop() {
    stop_ = true;
    journal_.notify();
    speech_changed_.notify_all();
    if (server_) server_->stop();
}

void Bus::shutdown() { set_stop(); }
}
