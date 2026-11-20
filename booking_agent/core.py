"""Transport-independent agent loop (used by Telegram and by the offline simulator)."""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Protocol

from . import voice
from .brain import Brain
from .config import Settings
from .decision_log import DecisionLog
from .state import Conversation


class Transport(Protocol):
    async def send_text(self, text: str) -> None: ...
    async def send_voice(self, ogg: Path, duration: int, caption: str) -> None: ...
    def typing(self, kind: str = "typing"): ...


class OwnerChannel(Protocol):
    async def ask(self, question: str, timeout: int) -> str | None: ...


class BookingAgent:
    def __init__(self, settings: Settings, conv: Conversation, transport: Transport,
                 owner: OwnerChannel, log: DecisionLog, conv_file: Path, echo=print):
        self.s = settings
        self.conv = conv
        self.brain = Brain(settings, conv)
        self.t = transport
        self.owner = owner
        self.log = log
        self.conv_file = conv_file
        self.echo = echo
        self.dirty = asyncio.Event()
        self.last_msg_at = 0.0
        self.manager_used_voice = False
        self.stopped = False  # LLM call limit reached

    # ---- input ----
    def on_manager_message(self, text: str, is_voice: bool = False) -> None:
        if len(text) > self.s.max_incoming_chars:  # untrusted input: cap what reaches the model
            text = text[:self.s.max_incoming_chars] + " [message cut: too long]"
        self.conv.add("manager", text, is_voice)
        self.manager_used_voice = is_voice
        self.last_msg_at = asyncio.get_running_loop().time()
        self.echo(f"MANAGER{' (voice)' if is_voice else ''}: {text}")
        self.save()
        self.dirty.set()

    # ---- loop ----
    async def start(self) -> None:
        await self.process("start", received="(agent starts the conversation)")

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await self.dirty.wait()
            while loop.time() - self.last_msg_at < self.s.debounce:  # let a burst of messages land
                await asyncio.sleep(0.3)
            self.dirty.clear()
            if self.conv.new_manager_turns() and not self.stopped:
                await self.process("manager")

    async def process(self, event: str, received: str | None = None, depth: int = 0) -> None:
        if received is None:
            news = self.conv.new_manager_turns()
            received = " / ".join(("[voice] " if t.voice else "") + t.text for t in news) or f"({event})"
        was_confirmed = self.conv.booking.status in ("confirmed", "finished")
        async with self.t.typing("typing"):
            d = await self.brain.step(event)
        just_confirmed = not was_confirmed and self.conv.booking.status in ("confirmed", "finished")

        if d.reply:
            await self.send(d.reply)
        decided = d.decision + (f" [code guard: {'; '.join(d.guards)}]" if d.guards else "")
        if just_confirmed:
            decided += " [booking confirmed]"
        self.log.write(received, d.understood, decided, d.reply or "(nothing sent)",
                       event=event, guards=d.guards, ask_owner=d.ask_owner,
                       booking=self.conv.booking.__dict__.copy())
        self.echo(f"   understood: {d.understood}\n   decided: {decided}")
        self.save()

        b = self.conv.booking
        if just_confirmed:
            await self.owner.notify(
                f"booked a table for {b.party_size} at {b.time} today, {b.date_long}, at "
                f"{b.restaurant_name}" + (f", under {b.guest_name}" if b.guest_name else "")
                + (". Notes: " + "; ".join(n.rstrip(". ") for n in b.notes) if b.notes else "") + ".")
        if d.limit_reached and not self.stopped:
            self.stopped = True
            await self.owner.notify(f"stopped replying to {self.conv.peer}: LLM call limit reached.")

        if d.ask_owner and depth < 5:
            self.echo(f"   -> asking owner: {d.ask_owner}")
            answer = await self.owner.ask(d.ask_owner, self.s.owner_timeout)
            if answer:
                self.conv.add("owner", answer)
                self.log.write(f"(owner) {answer}", "owner answered my question",
                               f"asked owner: {d.ask_owner}", "(nothing sent)", event="owner_answer")
                await self.process("owner", received=f"(owner answer) {answer}", depth=depth + 1)
            else:
                await self.process("owner_timeout", received="(owner did not answer)", depth=depth + 1)

    async def send(self, text: str) -> None:
        use_voice = self.s.voice_mode == "always" or (self.s.voice_mode == "mirror" and self.manager_used_voice)
        if use_voice:
            try:
                async with self.t.typing("record-audio"):
                    ogg, dur = await voice.synthesize(text, self.s.logs_dir / "voice", self.s.tts_voice)
                await self.t.send_voice(ogg, dur, caption=text)
                self.conv.add("agent", text, True)
                self.echo(f"AGENT (voice + caption): {text}")
                return
            except Exception as e:  # never lose a reply because of TTS
                self.echo(f"   TTS failed ({e}), sending text")
        await self.t.send_text(text)
        self.conv.add("agent", text)
        self.echo(f"AGENT: {text}")

    def save(self) -> None:
        with contextlib.suppress(Exception):
            self.conv.save(self.conv_file)
