"""Offline simulator: same agent and brain, no Telegram.

Interactive:  python run.py simulate            (you type as the manager)
Scripted:     python run.py simulate --script scenarios/deposit.txt

Script lines:  M: text   manager text message
               V: text   manager voice message (text -> edge-tts -> ogg -> Whisper -> agent)
               O: text   owner's answer to the agent's next question
               # comment
"""
from __future__ import annotations

import contextlib
from pathlib import Path

from . import voice
from .config import Settings
from .core import BookingAgent
from .decision_log import DecisionLog
from .owner import ConsoleOwner, ScriptOwner
from .state import Booking, Conversation, london_now


class ConsoleTransport:
    def __init__(self):
        self.voice_notes: list[Path] = []

    async def send_text(self, text: str) -> None:
        pass  # BookingAgent.echo already prints it

    async def send_voice(self, ogg: Path, duration: int, caption: str) -> None:
        self.voice_notes.append(ogg)
        print(f"   [voice note {duration}s: {ogg.name}]")

    def typing(self, kind: str = "typing"):
        return contextlib.nullcontext()


async def simulate(s: Settings, script: Path | None) -> None:
    name = f"sim_{script.stem if script else 'interactive'}_{london_now():%H%M%S}"
    conv = Conversation(peer="@simulated_manager", booking=Booking.for_today(s.owner_facts))
    lines = []
    if script:
        lines = [l.strip() for l in script.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.strip().startswith("#")]
    owner = ScriptOwner([l[2:].strip() for l in lines if l.startswith("O:")]) if script else ConsoleOwner()
    agent = BookingAgent(s, conv, ConsoleTransport(), owner,
                         DecisionLog(s.logs_dir, name, "@simulated_manager"),
                         s.logs_dir / f"{name}.conversation.json")
    await agent.start()

    async def feed(kind: str, text: str):
        if kind == "V":  # real voice round-trip to test the pipeline
            ogg, _ = await voice.synthesize(text, s.logs_dir / "voice", "en-GB-SoniaNeural")
            heard = await voice.transcribe(ogg, s.whisper_model)
            agent.on_manager_message(heard, is_voice=True)
        else:
            agent.on_manager_message(text)
        await agent.process("manager")

    if script:
        for l in lines:
            if l[:2] in ("M:", "V:"):
                await feed(l[0], l[2:].strip())
    else:
        print("Type the manager's messages. Prefix 'v ' to send it as a voice note. Empty line = quit.")
        while True:
            text = input("manager> ").strip()
            if not text:
                break
            await feed("V" if text.startswith("v ") else "M", text[2:] if text.startswith("v ") else text)
    print(f"\nBooking: {conv.booking}\nLog: {agent.log.md}")
