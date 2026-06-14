import json
import os
import hashlib


def _atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _path(data_dir: str, filename: str) -> str:
    return os.path.join(data_dir, filename)


# ── Processed IDs ────────────────────────────────────────────────────────────

def load_processed(data_dir: str) -> set:
    f = _path(data_dir, "processed_ids.json")
    if not os.path.exists(f):
        return set()
    try:
        content = open(f).read().strip()
        return set(json.loads(content)) if content else set()
    except (json.JSONDecodeError, OSError):
        return set()


def save_processed(data_dir: str, ids: set):
    _atomic_write(_path(data_dir, "processed_ids.json"), list(ids))


def is_new(activity_id: str, processed_ids: set) -> bool:
    return activity_id not in processed_ids


# ── Content hashes ───────────────────────────────────────────────────────────

def compute_record_hash(record: dict) -> str:
    hash_dict = {k: v for k, v in record.items() if k != "activity_id"}
    hash_str  = json.dumps(hash_dict, sort_keys=True, default=str)
    return hashlib.md5(hash_str.encode()).hexdigest()


def load_processed_hashes(data_dir: str) -> dict:
    f = _path(data_dir, "processed_hashes.json")
    if not os.path.exists(f):
        return {}
    try:
        with open(f) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_processed_hashes(data_dir: str, hashes: dict):
    _atomic_write(_path(data_dir, "processed_hashes.json"), hashes)


def has_record_changed(activity_id: str, record: dict, processed_hashes: dict) -> bool:
    stored = processed_hashes.get(activity_id)
    if stored is None:
        return True
    return compute_record_hash(record) != stored
