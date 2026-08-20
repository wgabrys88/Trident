#include "family.hpp"
#include "kv_cache.hpp"
#include "speaker_cache.hpp"

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

void expect(bool ok, const char* what) {
    if (!ok) throw std::runtime_error(what);
}

void test_family_policy() {
    tts::EngineKnobs knobs;
    knobs.language = "de";
    knobs.cfg_weight = 0.5f;
    knobs.exaggeration = 0.4f;
    knobs.top_k = 1000;
    expect(tts::apply_family_policy(tts::Family::Turbo, knobs), "turbo should force cfg/exag off");
    expect(knobs.cfg_weight == 0.f, "turbo cfg");
    expect(knobs.exaggeration == 0.f, "turbo exag");
    expect(knobs.language == "en", "turbo language");

    knobs.language = "de";
    knobs.cfg_weight = 0.5f;
    knobs.exaggeration = 0.3f;
    knobs.top_k = 40;
    expect(!tts::apply_family_policy(tts::Family::V3, knobs), "v3 should keep cfg/exag");
    expect(knobs.cfg_weight == 0.5f, "v3 cfg");
    expect(knobs.top_k == 0, "v3 top_k forced 0");
    expect(tts::language_id("en") == 708, "en id");
    expect(tts::language_id("de") == 636, "de id");
    expect(tts::language_id("pl") == 717, "pl id");
    expect(std::string(tts::policy(tts::Family::V3).name) == "v3", "v3 name");
}

void test_speaker_lru() {
    tts::SpeakerCache cache(2);
    tts::SpeakerEmbedding a, b, c;
    a.key = tts::SpeakerCache::make_key("a.wav", 1, "en");
    b.key = tts::SpeakerCache::make_key("b.wav", 1, "en");
    c.key = tts::SpeakerCache::make_key("c.wav", 1, "en");
    expect(a.key != b.key, "distinct keys");
    tts::SpeakerEmbedding out;
    expect(!cache.get(a.key, out), "cold miss");
    cache.put(a);
    cache.put(b);
    expect(cache.get(a.key, out), "a still hot");
    cache.put(c);
    expect(!cache.get(b.key, out), "b evicted");
    expect(cache.get(c.key, out), "c present");
    expect(cache.hits() >= 2, "hits");
    expect(cache.evictions() == 1, "one eviction");
}

void test_prefix_lru() {
    tts::PrefixKVCache cache(1);
    const std::vector<int32_t> tokens{255, 708, 12, 13};
    tts::PrefixKV one;
    one.key = tts::PrefixKVCache::make_key("v3", "en", tokens, 2);
    one.tokens = {255, 708};
    cache.put(one);
    tts::PrefixKV out;
    expect(cache.get(one.key, out), "prefix hit");
    tts::PrefixKV two;
    two.key = tts::PrefixKVCache::make_key("v3", "de", tokens, 2);
    cache.put(two);
    expect(!cache.get(one.key, out), "prefix evicted");
}

} // namespace

int main() {
    try {
        test_family_policy();
        test_speaker_lru();
        test_prefix_lru();
        std::cout << "trident-tts-tests ok\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "trident-tts-tests: " << error.what() << std::endl;
        return 1;
    }
}
