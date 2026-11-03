from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_HISTORY_PATH = Path(__file__).resolve().parent.parent / "sent_list.json"


def load_sent(path: Path = DEFAULT_HISTORY_PATH) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read sent history ({exc.__class__.__name__})") from exc
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise RuntimeError("Sent history must be a JSON array of URLs")
    return set(data)


def save_sent(links: set[str], path: Path = DEFAULT_HISTORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_text(
            json.dumps(sorted(links), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"Unable to save sent history ({exc.__class__.__name__})") from exc
