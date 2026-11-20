"""Booking state is owned by code, not by the model.

The model proposes updates; code decides which ones are allowed
(time / party size change only with the owner's approval, the date is
computed from the Europe/London clock and never by the model).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def london_now() -> datetime:
    return datetime.now(LONDON)


def long_date(d: datetime) -> str:
    return f"{d.strftime('%A')}, {d.day} {d.strftime('%B')}"  # e.g. "Friday, 20 November"


@dataclass
class Booking:
    party_size: int = 2
    time: str = "17:00"
    date_iso: str = ""
    date_long: str = ""
    restaurant_name: str | None = None
    guest_name: str | None = None
    status: str = "not_started"  # not_started | requested | waiting_owner | confirmed | finished
    approved_times: list[str] = field(default_factory=lambda: ["17:00"])
    notes: list[str] = field(default_factory=list)  # facts stated by the manager (deposit, hold time...)

    @classmethod
    def for_today(cls, facts: dict) -> "Booking":
        now = london_now()
        b = cls(date_iso=now.date().isoformat(), date_long=long_date(now))
        b.party_size = int(facts.get("party_size", 2))
        b.time = facts.get("time", "17:00")
        b.approved_times = [b.time]
        b.guest_name = facts.get("guest_name") or None
        b.restaurant_name = facts.get("restaurant_name") or None
        return b


@dataclass
class Turn:
    role: str  # agent | manager | owner | system
    text: str
    ts: str
    voice: bool = False


@dataclass
class Conversation:
    peer: str
    booking: Booking
    turns: list[Turn] = field(default_factory=list)
    processed_upto: int = 0  # index in turns already shown to the model as "new"

    def add(self, role: str, text: str, voice: bool = False) -> Turn:
        t = Turn(role, text, london_now().strftime("%H:%M:%S"), voice)
        self.turns.append(t)
        return t

    def new_manager_turns(self) -> list[Turn]:
        return [t for t in self.turns[self.processed_upto:] if t.role == "manager"]

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Conversation":
        raw = json.loads(path.read_text(encoding="utf-8"))
        conv = cls(peer=raw["peer"], booking=Booking(**raw["booking"]))
        conv.turns = [Turn(**t) for t in raw["turns"]]
        conv.processed_upto = raw.get("processed_upto", len(conv.turns))
        return conv
