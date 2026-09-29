import gguf
import numpy as np

TYPES = tuple(kind.name.lower() for kind in gguf.GGMLQuantizationType)


class Policy:
    def __init__(self, default, rules):
        self.default, self.rules = default, rules

    def add(self, writer, name, array):
        array = np.ascontiguousarray(array)
        if array.dtype.kind in "iu":
            writer.add_tensor(name, array)
            return
        selected = self.default
        for rule in self.rules:
            if (name.startswith(rule.get("prefix", "")) and name.endswith(rule.get("suffix", ""))
                    and rule.get("contains", "") in name and rule.get("ndim", array.ndim) == array.ndim):
                selected = rule["type"]
                break
        kind = gguf.GGMLQuantizationType[selected.upper()]
        packed = gguf.quants.quantize(array.astype(np.float32), kind)
        writer.add_tensor(name, packed, raw_dtype=kind)
