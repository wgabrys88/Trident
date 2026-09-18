from __future__ import annotations


def normalize_binary_records(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"invalid binaries metadata type: {type(value).__name__}")
    if not all(isinstance(record, dict) for record in value):
        raise TypeError("invalid binaries metadata record")
    return value


def find_binary_record(value, suffix: str) -> dict | None:
    suffix = suffix.lower()
    for record in normalize_binary_records(value):
        if str(record.get("path", "")).lower().endswith(suffix):
            return record
    return None
