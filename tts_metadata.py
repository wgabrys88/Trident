from __future__ import annotations


def normalize_binary_records(value) -> list[dict]:
    """Return the canonical list-of-file-identity records for build metadata.

    Broken release stamps used a path-keyed dict while RunEvidence expected a list.
    Accept both so existing caches remain usable, but callers should write the list form.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        records = list(value.values())
    elif isinstance(value, (list, tuple)):
        records = list(value)
    else:
        raise TypeError(f"invalid binaries metadata type: {type(value).__name__}")
    if not all(isinstance(record, dict) for record in records):
        raise TypeError("invalid binaries metadata record")
    return records


def find_binary_record(value, suffix: str) -> dict | None:
    suffix = suffix.lower()
    for record in normalize_binary_records(value):
        if str(record.get("path", "")).lower().endswith(suffix):
            return record
    return None
