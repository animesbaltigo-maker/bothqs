import json
from pathlib import Path

from config import DATA_DIR

SETTINGS_PATH = Path(DATA_DIR) / "admin_settings.json"


def _load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings(data: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_sticker_divisor(default: str = "") -> str:
    settings = _load_settings()
    value = str(settings.get("sticker_divisor") or "").strip()
    return value or str(default or "").strip()


def set_sticker_divisor(file_id: str) -> str:
    value = str(file_id or "").strip()
    if not value:
        raise ValueError("file_id do sticker vazio.")
    settings = _load_settings()
    settings["sticker_divisor"] = value
    _save_settings(settings)
    return value
