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

    # autonomous: the owner is busy; the agent acts within owner_profile.json and never waits.
    # ask: unknown things are asked from the owner (OWNER_CHANNEL) and the agent waits.
    owner_mode: str = _env("OWNER_MODE", "autonomous")
    # Where the owner gets questions (ask mode) and the final report: none | console | me | @username
    owner_channel: str = _env("OWNER_CHANNEL", "me")
    owner_timeout: int = int(_env("OWNER_TIMEOUT_SECONDS", "180"))
    debounce: float = float(_env("DEBOUNCE_SECONDS", "4"))

    llm_backend: str = _env("LLM_BACKEND", "codex")  # codex | openai
    codex_bin: str = _env("CODEX_BIN", "codex")
    codex_model: str = _env("CODEX_MODEL", "gpt-6.2-sol")
    codex_reasoning: str = _env("CODEX_REASONING", "low")
    openai_key: str = _env("OPENAI_API_KEY")
    openai_base: str = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = _env("OPENAI_MODEL", "gpt-4.1-mini")
    llm_timeout: int = int(_env("LLM_TIMEOUT_SECONDS", "60"))

    # Abuse limits: the manager's text is untrusted, these caps are enforced in code.
    max_llm_calls: int = int(_env("MAX_LLM_CALLS", "40"))          # per conversation
    max_incoming_chars: int = int(_env("MAX_INCOMING_CHARS", "800"))  # longer text is cut
    max_voice_seconds: int = int(_env("MAX_VOICE_SECONDS", "90"))     # longer voice is not transcribed
    max_reply_chars: int = int(_env("MAX_REPLY_CHARS", "450"))
    history_turns: int = int(_env("HISTORY_TURNS", "30"))             # transcript window sent to the model

    voice_mode: str = _env("VOICE_MODE", "mirror")  # off | mirror | always
    tts_voice: str = _env("TTS_VOICE", "en-GB-RyanNeural")
    whisper_model: str = _env("WHISPER_MODEL", "small")

    # The real manager. Writing to them needs an explicit --live flag.
    protected_target: str = _env("PROTECTED_TARGET", "vlad_dev_7").lstrip("@").lower()

    logs_dir: Path = ROOT / "logs"
    profile_file: Path = ROOT / "owner_profile.json"
    owner_facts: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.logs_dir.mkdir(exist_ok=True)
        src = self.profile_file if self.profile_file.exists() else ROOT / "owner_profile.example.json"
        self.owner_facts = json.loads(src.read_text(encoding="utf-8"))

    def secrets(self) -> list[str]:
        """Values that must never appear in a message to the manager."""
        vals = [self.api_hash, str(self.api_id) if self.api_id else "", self.openai_key]
        return [v for v in vals if v and len(v) >= 5]
