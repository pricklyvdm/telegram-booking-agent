"""Settings from .env. Secrets live only in .env and *.session (both git-ignored)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class Settings:
    api_id: int = int(_env("TG_API_ID", "0") or 0)
    api_hash: str = _env("TG_API_HASH")
    session: str = str(ROOT / (_env("TG_SESSION", "booking_agent")))

    owner_channel: str = _env("OWNER_CHANNEL", "console")  # console | me | @username
    owner_timeout: int = int(_env("OWNER_TIMEOUT_SECONDS", "900"))
    debounce: float = float(_env("DEBOUNCE_SECONDS", "4"))

    llm_backend: str = _env("LLM_BACKEND", "codex")  # codex | openai
    codex_bin: str = _env("CODEX_BIN", "codex")
    codex_model: str = _env("CODEX_MODEL")
    codex_reasoning: str = _env("CODEX_REASONING", "low")
    openai_key: str = _env("OPENAI_API_KEY")
    openai_base: str = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = _env("OPENAI_MODEL", "gpt-4.1-mini")

    voice_mode: str = _env("VOICE_MODE", "mirror")  # off | mirror | always
    tts_voice: str = _env("TTS_VOICE", "en-GB-RyanNeural")
    whisper_model: str = _env("WHISPER_MODEL", "small")

    # The real manager. Writing to them needs an explicit --live flag.
    protected_target: str = _env("PROTECTED_TARGET", "vlad_dev_7").lstrip("@").lower()

    logs_dir: Path = ROOT / "logs"
    facts_file: Path = ROOT / "owner_facts.json"
    owner_facts: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.logs_dir.mkdir(exist_ok=True)
        src = self.facts_file if self.facts_file.exists() else ROOT / "owner_facts.example.json"
        self.owner_facts = json.loads(src.read_text(encoding="utf-8"))
