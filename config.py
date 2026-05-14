import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _load_local_env() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return

    try:
        raw_lines = env_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return

    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


_load_local_env()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on", "sim", "s"}:
        return True
    if raw in {"0", "false", "no", "off", "nao", "n"}:
        return False
    return default


def _env_str_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, "").strip() or default
    raw = raw.replace(";", ",")
    return [item.strip() for item in raw.split(",") if item.strip()]


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "HQBaltigoBot").strip().lstrip("@")
BOT_BRAND = os.getenv("BOT_BRAND", "HQ Baltigo").strip() or "HQ Baltigo"

CATALOG_SITE_BASE = os.getenv("CATALOG_SITE_BASE", "https://www.hq-now.com").strip().rstrip("/")
CATALOG_API_URL = os.getenv("CATALOG_API_URL", "https://admin.hq-now.com/graphql").strip()

REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@HQs_Brasil").strip()
REQUIRED_CHANNELS = _env_str_list(
    "REQUIRED_CHANNELS",
    "@AtualizacoesOn,@HQs_Brasil,@QG_BALTIGO",
)
REQUIRED_CHANNEL_URL = os.getenv("REQUIRED_CHANNEL_URL", "https://t.me/HQs_Brasil").strip()
CANAL_POSTAGEM = os.getenv("CANAL_POSTAGEM", "").strip()
CANAL_POSTAGEM_HQS = os.getenv("CANAL_POSTAGEM_HQS", "").strip() or CANAL_POSTAGEM or "@HQs_Brasil"
CANAL_POSTAGEM_UPDATES = os.getenv("CANAL_POSTAGEM_UPDATES", "").strip() or CANAL_POSTAGEM
WEBAPP_BASE_URL = os.getenv("WEBAPP_BASE_URL", "").strip().rstrip("/")

ADMIN_IDS = [
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "").split(",")
    if value.strip().isdigit()
]

SEARCH_LIMIT = _env_int("SEARCH_LIMIT", 12)
SEARCH_PAGE_SIZE = _env_int("SEARCH_PAGE_SIZE", 8)
CHAPTERS_PER_PAGE = _env_int("CHAPTERS_PER_PAGE", 15)
POPULAR_LIMIT = _env_int("POPULAR_LIMIT", 60)
UPDATES_LIMIT = _env_int("UPDATES_LIMIT", 40)
HOME_SECTION_LIMIT = _env_int("HOME_SECTION_LIMIT", 8)
HISTORY_PAGE_SIZE = _env_int("HISTORY_PAGE_SIZE", 8)
FAVORITES_PAGE_SIZE = _env_int("FAVORITES_PAGE_SIZE", 8)
PUBLISHERS_PAGE_SIZE = _env_int("PUBLISHERS_PAGE_SIZE", 8)
PUBLISHER_DISCOVERY_MAX_ID = _env_int("PUBLISHER_DISCOVERY_MAX_ID", 18)

HTTP_TIMEOUT = _env_int("HTTP_TIMEOUT", 35)
API_CACHE_TTL_SECONDS = _env_int("API_CACHE_TTL_SECONDS", 900)
SEARCH_SESSION_TTL_SECONDS = _env_int("SEARCH_SESSION_TTL_SECONDS", 7200)
ANTI_FLOOD_SECONDS = _env_float("ANTI_FLOOD_SECONDS", 0.9)

PDF_CACHE_DIR = str(DATA_DIR / "pdf_cache")
EPUB_CACHE_DIR = str(DATA_DIR / "epub_cache")
PDF_NAME_PATTERN = os.getenv("PDF_NAME_PATTERN", "{title} - Capitulo {chapter} - @HQs_Brasil.pdf").strip()
EPUB_NAME_PATTERN = os.getenv("EPUB_NAME_PATTERN", "{title} - Capitulo {chapter} - @HQs_Brasil.epub").strip()
PDF_QUEUE_LIMIT = _env_int("PDF_QUEUE_LIMIT", 60)
PDF_WORKERS_SINGLE = _env_int("PDF_WORKERS_SINGLE", 2)
PDF_WORKERS_BULK = _env_int("PDF_WORKERS_BULK", 1)
PDF_PROTECT_CONTENT = _env_bool("PDF_PROTECT_CONTENT", True)

TELEGRAPH_AUTHOR = os.getenv("TELEGRAPH_AUTHOR", BOT_BRAND).strip() or BOT_BRAND
PROMO_BANNER_URL = os.getenv(
    "PROMO_BANNER_URL",
    "https://photo.chelpbot.me/AgACAgEAAxkBa-wKPmn-ZprlQctPM-MQNYtBwlJimld5AALfC2sb2U34R-qFUhmN9z82AQADAgADeQADOwQ/photo.jpg",
).strip()
STICKER_DIVISOR = os.getenv("STICKER_DIVISOR", "").strip()
DISTRIBUTION_TAG = os.getenv("DISTRIBUTION_TAG", "@HQs_Brasil").strip() or "@HQs_Brasil"
