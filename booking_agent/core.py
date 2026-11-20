"""Transport-independent agent loop (used by Telegram and by the offline simulator)."""
from __future__ import annotations

import asyncio
import contextlib
import copy
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
        self.lock = asyncio.Lock()  # one turn at a time (messages, silence follow-ups)
        self.last_sent_at = 0.0
        self.followed_up = False
        self.gave_up = False

    # ---- input ----
    def on_manager_message(self, text: str, is_voice: bool = False) -> None:
        if len(text) > self.s.max_incoming_chars:  # untrusted input: cap what reaches the model
            text = text[:self.s.max_incoming_chars] + " [message cut: too long]"
        self.conv.add("manager", text, is_voice)
        self.manager_used_voice = is_voice
        self.last_msg_at = asyncio.get_running_loop().time()
        self.followed_up = self.gave_up = False
        self.echo(f"MANAGER{' (voice)' if is_voice else ''}: {text}")
        self.save()
        self.dirty.set()

    # ---- loop ----
    async def start(self) -> None:
        async with self.lock:
            await self.process("start", received="(agent starts the conversation)")

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await self.dirty.wait()
            while loop.time() - self.last_msg_at < self.s.debounce:  # let a burst of messages land
                await asyncio.sleep(0.3)
            self.dirty.clear()
            if self.conv.new_manager_turns() and not self.stopped:
                async with self.lock:
                    await self.process("manager")

    async def watch_silence(self) -> None:
        """Manager stopped answering before the booking is confirmed: one polite follow-up,
        then, if still nothing, tell the owner. The agent keeps listening either way."""
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(15)
            b = self.conv.booking
            if (self.stopped or self.lock.locked() or b.status in ("confirmed", "finished", "closed")
                    or not self.conv.turns or self.conv.turns[-1].role != "agent"):
                continue
            quiet = (loop.time() - self.last_sent_at) / 60
            if not self.followed_up and quiet >= self.s.silence_followup_min:
                self.followed_up = True
                async with self.lock:
                    await self.process("silence", received=f"(manager silent for {int(quiet)} min)")
            elif self.followed_up and not self.gave_up and quiet >= self.s.silence_giveup_min:
                self.gave_up = True
                self.log.write(f"(manager silent for {int(quiet)} min after a follow-up)", "no reply",
                               "report to the owner, keep listening", "(nothing sent)", event="give_up")
                await self.owner.notify(f"{self.conv.peer} has not replied for {int(quiet)} minutes "
                                        "after a follow-up. Booking not confirmed; I keep listening.")

    async def process(self, event: str, received: str | None = None, depth: int = 0) -> None:
        given = received
        was_confirmed = self.conv.booking.status in ("confirmed", "finished")
        for attempt in range(3):
            news = self.conv.new_manager_turns()
            received = given or " / ".join(("[voice] " if t.voice else "") + t.text for t in news) or f"({event})"
            snap = (copy.deepcopy(self.conv.booking), self.brain.agreed_pending,
                    self.brain.last_question, self.conv.processed_upto)
            async with self.t.typing("typing"):
                d = await self.brain.step(event)
            fresh = [t for t in self.conv.turns[self.brain.last_boundary:] if t.role == "manager"]
            if not fresh or event == "start" or attempt == 2:
                break
            # The manager wrote again while the model was thinking (e.g. took the offer back):
            # throw the stale draft away, restore the state and decide on the full picture.
            (self.conv.booking, self.brain.agreed_pending,
             self.brain.last_question, self.conv.processed_upto) = snap
            self.echo("   (manager wrote again while I was thinking: re-deciding)")
            if event in ("owner", "owner_timeout", "silence"):
                event = "manager"
                given = None
        if d.error and event == "manager":
            # model failed twice: keep the messages unhandled and retry in a moment
            self.conv.processed_upto = snap[3]
            self.log.write(received, "", d.decision + " [will retry]", "(nothing sent)", event="error")
            asyncio.get_running_loop().call_later(20, self.dirty.set)
            return
        just_confirmed = not was_confirmed and self.conv.booking.status in ("confirmed", "finished")
        if given is None and len(news) > 1:  # one log row per incoming message, as the brief asks
            for t in news[:-1]:
                self.log.write(("[voice] " if t.voice else "") + t.text, "(read together with the next message)",
                               "(answered together in the next row)", "(see next row)", event="manager")
            received = ("[voice] " if news[-1].voice else "") + news[-1].text

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
        if b.status == "closed" and d.conversation_over and not d.limit_reached:
            await self.owner.notify(f"no table at {self.conv.peer} within your limits today; I closed politely. "
                                    "If they come back with an offer, I'll handle it.")
        if d.cancelled:
            await self.owner.notify(f"{self.conv.peer} cancelled the confirmed booking. "
                                    "Looking for the nearest alternative within your limits.")
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
                self.last_sent_at = asyncio.get_running_loop().time()
                self.echo(f"AGENT (voice + caption): {text}")
                return
            except Exception as e:  # never lose a reply because of TTS
                self.echo(f"   TTS failed ({e}), sending text")
        await self.t.send_text(text)
        self.conv.add("agent", text)
        self.last_sent_at = asyncio.get_running_loop().time()
        self.echo(f"AGENT: {text}")

    def save(self) -> None:
        with contextlib.suppress(Exception):
            self.conv.save(self.conv_file)
