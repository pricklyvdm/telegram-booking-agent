"""Telegram transport: a user account (Telethon), because a Bot API bot cannot
write first, and the manager never writes first."""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeAudio

from . import voice
from .config import Settings
from .core import BookingAgent
from .decision_log import DecisionLog
from .owner import ConsoleOwner, TelegramOwner
from .state import Booking, Conversation, london_now


def make_client(s: Settings) -> TelegramClient:
    if not s.api_id or not s.api_hash:
        raise SystemExit("TG_API_ID / TG_API_HASH are missing in .env")
    return TelegramClient(s.session, s.api_id, s.api_hash)


async def login(s: Settings) -> None:
    client = make_client(s)
    await client.start()  # asks for phone, code and 2FA password in this terminal
    me = await client.get_me()
    print(f"Logged in as {me.first_name or ''} {me.last_name or ''} (@{me.username}). Session saved.")
    await client.disconnect()


class TgTransport:
    def __init__(self, client: TelegramClient, peer):
        self.client, self.peer = client, peer

    async def send_text(self, text: str) -> None:
        await self.client.send_message(self.peer, text)

    async def send_voice(self, ogg: Path, duration: int, caption: str) -> None:
        await self.client.send_file(
            self.peer, str(ogg), voice_note=True, caption=caption,
            attributes=[DocumentAttributeAudio(duration=duration, voice=True)])

    def typing(self, kind: str = "typing"):
        return self.client.action(self.peer, kind)


async def run(s: Settings, target: str, live: bool, resume: bool, force: bool) -> None:
    client = make_client(s)
    await client.start()
    peer = await client.get_entity(target)
    uname = (getattr(peer, "username", None) or str(peer.id)).lower()
    if uname == s.protected_target and not live:
        raise SystemExit(f"@{uname} is the real manager. Add --live only after the owner says go.")

    now = london_now()
    if not resume and not force and now.strftime("%H:%M") >= s.owner_facts.get("time", "17:00"):
        raise SystemExit(f"It is already {now:%H:%M} in London: 17:00 today has passed. Use --force to try anyway.")

    run_name = f"{now:%Y-%m-%d}_{uname}"
    conv_file = s.logs_dir / f"{run_name}.conversation.json"
    if resume and conv_file.exists():
        conv = Conversation.load(conv_file)
    else:
        conv = Conversation(peer=f"@{uname}", booking=Booking.for_today(s.owner_facts))

    if s.owner_channel == "console":
        owner = ConsoleOwner()
        owner_peer = None
    else:
        owner_peer = await client.get_entity(s.owner_channel)
        owner = TelegramOwner(client, owner_peer)

    agent = BookingAgent(s, conv, TgTransport(client, peer), owner,
                         DecisionLog(s.logs_dir, run_name, f"@{uname}"), conv_file)

    @client.on(events.NewMessage(chats=peer, incoming=True))
    async def on_manager(ev):
        msg = ev.message
        if msg.voice or msg.audio:
            path = Path(await msg.download_media(file=str(s.logs_dir / "voice" / f"in_{msg.id}.ogg")))
            text = await voice.transcribe(path, s.whisper_model)
            agent.on_manager_message(text or "(inaudible voice message)", is_voice=True)
        elif msg.raw_text:
            agent.on_manager_message(msg.raw_text)
        else:
            kind = "sticker" if msg.sticker else "photo" if msg.photo else "attachment"
            agent.on_manager_message(f"(sent a {kind} without text)")
        with contextlib.suppress(Exception):
            await client.send_read_acknowledge(peer, msg)

    if owner_peer is not None:
        @client.on(events.NewMessage(chats=owner_peer))
        async def on_owner(ev):
            owner.feed(ev.message.id, ev.message.raw_text)

    print(f"Agent is talking to @{uname}. Booking: {conv.booking.party_size} people, "
          f"{conv.booking.time}, {conv.booking.date_long}. Owner channel: {s.owner_channel}. Ctrl+C to stop.")
    if s.voice_mode != "off":  # warm up Whisper so the first voice note is quick
        asyncio.get_running_loop().run_in_executor(None, voice._load_model, s.whisper_model)
    if not resume:
        await agent.start()
    await asyncio.gather(agent.run_forever(), client.run_until_disconnected())
