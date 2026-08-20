ASR_RUNTIME = {"threads": 4, "device": "Vulkan0"}

BRAIN_MODEL = "gemma"
BRAIN_RUNTIME = {
    "device": "Vulkan0", "gpu_layers": "all", "context": 4096,
    "flash_attn": "on", "fit": "on", "fit_target": 1024, "fit_ctx": 4096,
}
BRAIN_GENERATION = {
    "temperature": 0.3, "top_p": 0.90, "top_k": 40, "min_p": 0.0,
    "repeat_penalty": 1.05, "seed": 42, "max_tokens": 1024,
}
BRAIN_THINKING = False
BRAIN_SYSTEM = (
    "Answer only in {language_name} ({language}). The user may have spoken "
    "another language; still answer only in {language_name}. Spoken prose: "
    "short sentences that end with a period, question mark, or exclamation. "
    "No markdown, lists, code, URLs, emoji, or square-bracket tags. Expand "
    "numbers and abbreviations. Match the user's level of detail. Do not "
    "mention transcription, models, or reasoning."
)

_EN = {"en": "English"}
_V3_SAMPLE = {
    "seed": 42, "max_tokens": 768, "top_p": 0.95,
    "temperature": 0.8, "repeat_penalty": 1.2,
    "min_p": 0.05, "cfm_steps": 7,
}

_TURBO_SAMPLE = {
    "seed": 42, "max_tokens": 768, "top_k": 1000, "top_p": 0.95,
    "temperature": 0.8, "repeat_penalty": 1.2,
    "cfm_steps": 2,
}

_NANO_SAMPLE = {
    "seed": 42, "max_tokens": 768, "top_k": 1000, "top_p": 0.95,
    "temperature": 0.8, "repeat_penalty": 1.2,
    "cfm_steps": 2,
}
_V3_CKPT = (
    "ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen_v3.pt",
    "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json",
)
_TURBO_CKPT = (
    "t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
)
_NANO_CKPT = (
    "t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
)


# Fairbanks (1960) "Rainbow Passage" calibration text (~2 min spoken), public
# domain; en is the canonical text, pl/de are Trident translations for the v3
# multilingual legs. Used by `main.py rainbow`.
RAINBOW = {
    "en": (
        "When the sunlight strikes raindrops in the air, they act as a prism and form a rainbow. "
        "The rainbow is a division of white light into many beautiful colors. These take the shape of a long "
        "round arch, with its path high above, and its two ends apparently beyond the horizon. There is, "
        "according to legend, a boiling pot of gold at one end. People look, but no one ever finds it. When a "
        "man looks for something beyond his reach, his friends say he is looking for the pot of gold at the end "
        "of the rainbow. Throughout the centuries people have explained the rainbow in various ways. Some have "
        "accepted it as a miracle without physical explanation. To the Hebrews it was a token that there would "
        "be no more universal floods. The Greeks used to imagine that it was a sign from the gods to foretell "
        "war or heavy rain. The Norsemen considered the rainbow as a bridge over which the gods passed from "
        "earth to their home in the sky. Others have tried to explain the phenomenon physically. Aristotle "
        "thought that the rainbow was caused by reflection of the sun's rays by the rain. Since then physicists "
        "have found that it is not reflection, but refraction by the raindrops which causes the rainbows. Many "
        "complicated ideas about the rainbow have been formed. The difference in the rainbow depends considerably "
        "upon the size of the drops, and the width of the colored band increases as the size of the drops "
        "increases. The actual primary rainbow observed is said to be the effect of super-imposition of a number "
        "of bows. If the red of the second bow falls upon the green of the first, the result is to give a bow "
        "with an abnormally wide yellow band, since red and green light when mixed form yellow. This is a very "
        "common type of bow, one showing mainly red and yellow, with little or no green or blue."
    ),
    "pl": (
        "Gdy światło słoneczne pada na krople deszczu w powietrzu, działają one jak pryzmat i tworzą tęczę. "
        "Tęcza jest podziałem białego światła na wiele pięknych kolorów. Przybierają one kształt długiego, "
        "okrągłego łuku, którego szczyt wznosi się wysoko, a oba końce zdają się sięgać poza horyzont. Zgodnie z "
        "legendą, na jednym z końców znajduje się wrzący garnek złota. Ludzie szukają, ale nikt nigdy go nie "
        "znalazł. Gdy ktoś szuka czegoś poza swoim zasięgiem, przyjaciele mówią, że szuka garnka złota na końcu "
        "tęczy. Przez wieki ludzie wyjaśniali tęczę na różne sposoby. Niektórzy uznawali ją za cud bez "
        "fizycznego wyjaśnienia. Dla Hebrajczyków była znakiem, że nie będzie już więcej powszechnego potopu. "
        "Grecy wyobrażali sobie, że jest to znak od bogów zapowiadający wojnę lub ulewę. Normanowie uważali "
        "tęczę za most, po którym bogowie przechodzili z ziemi do swojego domu na niebie. Inni próbowali "
        "wyjaśnić to zjawisko fizycznie. Arystoteles sądził, że tęcza powstaje w wyniku odbicia promieni słońca "
        "przez deszcz. Od tamtej pory fizycy odkryli, że to nie odbicie, lecz załamanie światła przez krople "
        "deszczu powoduje powstawanie tęczy. Powstało wiele skomplikowanych wyjaśnień tęczy. Różnice w tęczy "
        "zależą w znacznym stopniu od wielkości kropli, a szerokość kolorowego pasma wzrasta wraz ze wzrostem "
        "wielkości kropli. Twierdzi się, że obserwowana tęcza główna jest efektem nałożenia się wielu tęcz. "
        "Jeśli czerwień drugiej tęczy pada na zieleń pierwszej, wynikiem jest tęcza o nienormalnie szerokim "
        "żółtym paśmie, ponieważ światło czerwone i zielone zmieszane dają żółty. Jest to bardzo powszechny typ "
        "tęczy, pokazujący głównie czerwień i żółć, z niewielką ilością zieleni lub błękitu, albo wcale."
    ),
    "de": (
        "Wenn das Sonnenlicht auf Regentropfen in der Luft trifft, wirken sie wie ein Prisma und bilden einen "
        "Regenbogen. Der Regenbogen ist eine Zerlegung des weißen Lichts in viele schöne Farben. Diese nehmen "
        "die Form eines langen runden Bogens an, dessen Scheitel hoch oben liegt und dessen beide Enden "
        "scheinbar jenseits des Horizonts liegen. Der Legende nach befindet sich an einem Ende ein kochender "
        "Topf voll Gold. Die Menschen suchen, aber niemand hat ihn je gefunden. Wenn jemand nach etwas sucht, "
        "das außerhalb seiner Reichweite liegt, sagen seine Freunde, er suche den Topf voll Gold am Ende des "
        "Regenbogens. Im Laufe der Jahrhunderte haben die Menschen den Regenbogen auf verschiedene Weise "
        "erklärt. Manche haben ihn als ein Wunder ohne physikalische Erklärung akzeptiert. Für die Hebräer war "
        "er ein Zeichen dafür, dass es keine weitere weltumspannende Flut mehr geben würde. Die Griechen "
        "stellten sich vor, er sei ein Zeichen der Götter, das Krieg oder schweren Regen vorhersagte. Die "
        "Nordmänner betrachteten den Regenbogen als eine Brücke, über die die Götter von der Erde in ihre "
        "Heimat am Himmel zogen. Andere haben versucht, das Phänomen physikalisch zu erklären. Aristoteles "
        "glaubte, der Regenbogen werde durch die Reflexion der Sonnenstrahlen durch den Regen verursacht. "
        "Seitdem haben Physiker herausgefunden, dass es nicht die Reflexion, sondern die Brechung durch die "
        "Regentropfen ist, die den Regenbogen hervorruft. Viele komplizierte Vorstellungen über den Regenbogen "
        "wurden gebildet. Der Unterschied im Regenbogen hängt erheblich von der Größe der Tropfen ab, und die "
        "Breite des farbigen Bandes nimmt zu, wenn die Größe der Tropfen zunimmt. Der tatsächlich beobachtete "
        "Hauptregenbogen soll die Wirkung der Überlagerung mehrerer Bögen sein. Wenn das Rot des zweiten Bogens "
        "auf das Grün des ersten fällt, entsteht ein Bogen mit einem ungewöhnlich breiten gelben Band, da rotes "
        "und grünes Licht gemischt Gelb ergeben. Dies ist eine sehr häufige Art des Bogens, die hauptsächlich "
        "Rot und Gelb zeigt, mit wenig oder keinem Grün oder Blau."
    ),
}


def _gguf(label, repo, revision, file, size, script, files, quant="q4_0", variant=None, copy=None):
    convert = {"script": script, "quant": quant, "files": files}
    if variant:
        convert["variant"] = variant
    if copy:
        convert["copy"] = copy
    return {"label": label, "repo": repo, "revision": revision, "file": file, "size": size, "convert": convert}


def _family(label, languages, context, chars, sample_cfg, voice_cfg, models):
    return {
        "TTS_LANGUAGES": languages,
        "DEFAULT_REPLY_LANGUAGE": "en",
        "TTS_LABEL": label,
        "TTS_RUNTIME": {"gpu_layers": 99, "context": context, "threads": 4},
        "TTS_SAMPLE": sample_cfg,
        "TTS_VOICE": voice_cfg,
        "TTS_CHUNK": {"chars": chars},
        "TTS_MODELS": models,
    }


FAMILIES = {
    "v3": _family(
        "CHATTERBOX TTS V3", {"en": "English", "pl": "Polish", "de": "German"},
        2048, 180,
        _V3_SAMPLE,
        {"cfg_weight": 0.5, "exaggeration": 0.3},
        {
            "chatterbox-t3": _gguf(
                "CHATTERBOX V3 T3", "ResembleAI/chatterbox",
                "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
                "chatterbox-t3-mtl-v3-q4_0.gguf", 344985408,
                "convert-t3-mtl-to-gguf.py", _V3_CKPT,
                copy={"t3_mtl23ls_v3.safetensors": "t3_mtl23ls_v2.safetensors"},
            ),
            "chatterbox-codec": _gguf(
                "CHATTERBOX V3 S3GEN", "ResembleAI/chatterbox",
                "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
                "chatterbox-s3gen-mtl-v3-f16.gguf", 1056431360,
                "convert-s3gen-to-gguf.py", _V3_CKPT, quant="f16", variant="mtl",
                copy={"s3gen_v3.pt": "s3gen.pt"},
            ),
        },
    ),
    "turbo": _family(
        "CHATTERBOX TTS TURBO", dict(_EN),
        2048, 120,
        _TURBO_SAMPLE,
        {"cfg_weight": 0.0, "exaggeration": 0.0},
        {
            "chatterbox-t3": _gguf(
                "CHATTERBOX TURBO T3", "ResembleAI/chatterbox-turbo",
                "749d1c1a46eb10492095d68fbcf55691ccf137cd",
                "chatterbox-t3-turbo-q4_0.gguf", 333506240,
                "convert-t3-turbo-to-gguf.py", _TURBO_CKPT,
            ),
            "chatterbox-codec": _gguf(
                "CHATTERBOX TURBO S3GEN", "ResembleAI/chatterbox-turbo",
                "749d1c1a46eb10492095d68fbcf55691ccf137cd",
                "chatterbox-s3gen-turbo-f16.gguf", 1064879936,
                "convert-s3gen-to-gguf.py", _TURBO_CKPT, quant="f16", variant="turbo",
            ),
        },
    ),
    "nano": _family(
        "CHATTERBOX TTS NANO", dict(_EN),
        2048, 180,
        _NANO_SAMPLE,
        {"cfg_weight": 0.0, "exaggeration": 0.0},
        {
            "chatterbox-t3": _gguf(
                "CHATTERBOX NANO T3", "ResembleAI/chatterbox-nano",
                "71ccd1d0081b430592cea481f4307e764e07bc64",
                "chatterbox-t3-nano-q4_0.gguf", 171901536,
                "convert-t3-turbo-to-gguf.py", _NANO_CKPT,
                copy={"t3_nano_v1.safetensors": "t3_turbo_v1.safetensors"},
            ),
            "chatterbox-codec": _gguf(
                "CHATTERBOX NANO S3GEN", "ResembleAI/chatterbox-nano",
                "71ccd1d0081b430592cea481f4307e764e07bc64",
                "chatterbox-s3gen-nano-f16.gguf", 1064879936,
                "convert-s3gen-to-gguf.py", _NANO_CKPT, quant="f16", variant="turbo",
            ),
        },
    ),
}
