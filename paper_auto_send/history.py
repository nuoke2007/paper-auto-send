from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_HISTORY_PATH = Path(__file__).resolve().parent.parent / "sent_list.json"
DEFAULT_CATALOG_PATH = DEFAULT_HISTORY_PATH.with_name("resource_catalog.json")


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read resource catalog ({exc.__class__.__name__})") from exc
    if not isinstance(data, list) or not all(
        isinstance(item, dict)
        and isinstance(item.get("title"), str) and isinstance(item.get("link"), str)
        and all(isinstance(value, str) for value in item.values())
        for item in data
    ):
        raise RuntimeError("Resource catalog must be an array of resource objects with string fields")
    return data


def save_catalog(papers: list[dict[str, str]], path: Path = DEFAULT_CATALOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_text(json.dumps(papers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_path, path)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Unable to save resource catalog ({exc.__class__.__name__})") from exc


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
