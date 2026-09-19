import argparse
import ast
import importlib.util
import json
import struct
import sys
from pathlib import Path


class TokenizerWorker:
    def __init__(self, args):
        self.language = args.language
        source = Path(args.tts_source).resolve()
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        punctuation = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "punc_norm")
        scope = {}
        exec(compile(ast.Module(body=[punctuation], type_ignores=[]), str(source), "exec"), scope)
        self.punctuation = scope["punc_norm"]
        spec = importlib.util.spec_from_file_location("chatterbox_official_tokenizer", Path(args.source).resolve())
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cangjie = json.loads(Path(args.cangjie).read_text(encoding="utf-8"))

        def load_cangjie(instance, model_dir=None):
            instance.word2cj, instance.cj2word = {}, {}
            for entry in cangjie:
                word, code = entry.split("\t")[:2]
                instance.word2cj[word] = code
                instance.cj2word.setdefault(code, []).append(word)

        module.ChineseCangjieConverter._load_cangjie_mapping = load_cangjie
        if self.language == "he":
            from dicta_onnx import Dicta
            module._dicta = Dicta(str(Path(args.dicta_model).resolve()))
        elif self.language == "ja":
            import pykakasi
            module._kakasi = pykakasi.kakasi()
        elif self.language == "ru":
            from russian_text_stresser.text_stresser import RussianTextStresser
            module._russian_stresser = RussianTextStresser()
        elif self.language == "zh":
            import spacy_pkuseg
        self.tokenizer = module.MTLTokenizer(str(Path(args.tokenizer).resolve()))
        self.input = sys.stdin.buffer
        self.output = sys.stdout.buffer

    def read(self, size):
        data = bytearray()
        while len(data) < size:
            chunk = self.input.read(size - len(data))
            if not chunk:
                raise EOFError("tokenizer request ended before its declared length")
            data.extend(chunk)
        return bytes(data)

    def reply(self, mode, text):
        if mode == b"R":
            return b"ready"
        if mode == b"P":
            return self.punctuation(text).encode("utf-8")
        if mode == b"T":
            tokens = self.tokenizer.encode(text, language_id=self.language)
            return struct.pack(f"<I{len(tokens)}i", len(tokens), *tokens)
        raise ValueError("unknown tokenizer request mode")

    def serve(self):
        while mode := self.input.read(1):
            size, = struct.unpack("<I", self.read(4))
            payload = self.reply(mode, self.read(size).decode("utf-8"))
            self.output.write(struct.pack("<II", 0, len(payload)) + payload)
            self.output.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("source", "tts-source", "tokenizer", "cangjie", "dicta-model", "language"):
        parser.add_argument("--" + name, required=True)
    TokenizerWorker(parser.parse_args()).serve()
