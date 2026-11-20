"""One decision per event: model proposes, code guards.

Code-level guards (the model cannot talk its way past them):
- time / party size / guest name change only with the owner's approval;
- no number (phone, card...) that is not in the owner's facts leaves the agent;
- when something is unknown the agent asks the owner and sends a holding line;
- the final confirmation must contain restaurant, date, time and party size,
  otherwise code appends a recap built from the booking state.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from . import llm
from .config import Settings
from .prompts import EVENT_HINTS, SYSTEM
from .state import Conversation, london_now

NUM_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
HOLDING = "Of course, one moment please while I check that."


@dataclass
class Decision:
    event: str
    understood: str
    decision: str
    reply: str
    ask_owner: str | None
    manager_agreed: bool
    conversation_over: bool
    guards: list[str] = field(default_factory=list)


def _minutes(hhmm: str) -> int | None:
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*$", hhmm or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


_TIME_RE = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)\b|\b(\d{1,2})[:.](\d{2})\b", re.I)
_YES_RE = re.compile(r"^\s*(yes|yeah|yep|ok|okay|sure|fine|good|go ahead|да|ок|давай|норм|подходит)\b", re.I)


def norm_time(text: str | None) -> str | None:
    """'5:30pm' / '17.30' / '5 pm' -> '17:30'. None if no time found."""
    times = extract_times(text or "")
    return times[0] if times else None


def extract_times(text: str) -> list[str]:
    out = []
    for m in _TIME_RE.finditer(text):
        if m.group(3):
            h, mi = int(m.group(1)), int(m.group(2) or 0)
            h = h % 12 + (12 if m.group(3).lower() == "pm" else 0)
        else:
            h, mi = int(m.group(4)), int(m.group(5))
        if h < 24 and mi < 60:
            out.append(f"{h:02d}:{mi:02d}")
    return out


def time_variants(hhmm: str) -> set[str]:
    mins = _minutes(hhmm)
    if mins is None:
        return {hhmm.lower()}
    h, m = divmod(mins, 60)
    h12 = h % 12 or 12
    ap = "pm" if h >= 12 else "am"
    out = {f"{h:02d}:{m:02d}", f"{h}:{m:02d}", f"{h12}:{m:02d}{ap}", f"{h12}:{m:02d} {ap}",
           f"{h:02d}.{m:02d}"}
    if m == 0:
        out |= {f"{h12}{ap}", f"{h12} {ap}", f"{h12} o'clock"}
    return out


def recap(b) -> str:
    who = NUM_WORDS.get(b.party_size, str(b.party_size))
    s = f"Just to confirm: a table for {who} at {b.time} today, {b.date_long}, at {b.restaurant_name}"
    if b.guest_name:
        s += f", under the name {b.guest_name}"
    return s + ". Thank you very much!"


def recap_is_complete(text: str, b) -> bool:
    t = text.lower()
    day, month = b.date_long.split(", ")[1].split(" ")
    has_time = any(v in t for v in time_variants(b.time))
    has_people = bool(re.search(rf"\b({b.party_size}|{NUM_WORDS.get(b.party_size, '#')})\b", t))
    has_date = day in t and month.lower() in t
    has_place = bool(b.restaurant_name) and b.restaurant_name.lower() in t
    return has_time and has_people and has_date and has_place


class Brain:
    def __init__(self, settings: Settings, conv: Conversation):
        self.s = settings
        self.conv = conv
        self.agreed_pending = False  # manager agreed but we could not recap yet (no restaurant name)
        self.last_question: str | None = None  # last question sent to the owner

    # ---------- prompt ----------
    def _system(self) -> str:
        b, f = self.conv.booking, self.s.owner_facts
        flex = int(f.get("time_flex_minutes", 0) or 0)
        flex_rule = (f"a time within {flex} minutes of {b.time} may be accepted without asking"
                     if flex else "none, always ask")
        return SYSTEM.format(party=NUM_WORDS.get(b.party_size, b.party_size), time=b.time,
                             date_long=b.date_long, flex_rule=flex_rule,
                             guest_ref=b.guest_name or "a guest")

    def _user(self, event: str) -> str:
        now = london_now()
        greeting = "good morning" if now.hour < 12 else "good afternoon" if now.hour < 18 else "good evening"
        lines = []
        for i, t in enumerate(self.conv.turns):
            tag = t.role.upper() + (" (voice message, transcribed)" if t.voice else "")
            new = "  <-- NEW" if i >= self.conv.processed_upto and t.role == "manager" else ""
            lines.append(f"[{t.ts}] {tag}: {t.text}{new}")
        facts = {k: v for k, v in self.s.owner_facts.items() if not k.startswith("_")}
        return "\n".join([
            f"London time now: {now.strftime('%H:%M')}, {now.strftime('%A %d %B %Y')}.",
            f"BOOKING STATE: {json.dumps(asdict(self.conv.booking), ensure_ascii=False)}",
            f"OWNER FACTS: {json.dumps(facts, ensure_ascii=False)}",
            "TRANSCRIPT:",
            "\n".join(lines) or "(empty, nobody has written yet)",
            "",
            EVENT_HINTS[event].format(greeting=greeting),
        ])

    # ---------- step ----------
    async def step(self, event: str) -> Decision:
        system, user = self._system(), self._user(event)
        try:
            raw = await llm.complete(self.s, system, user)
        except Exception as e:  # one retry, then a safe holding line
            try:
                raw = await llm.complete(self.s, system, user)
            except Exception as e2:
                raw = {"understood": "", "decision": f"LLM error: {e2}", "reply": "",
                       "ask_owner": None, "updates": {}, "manager_agreed": False,
                       "conversation_over": False}
        self.conv.processed_upto = len(self.conv.turns)
        return self._apply(raw, event)

    def _apply(self, raw: dict, event: str) -> Decision:
        b = self.conv.booking
        d = Decision(event=event, understood=raw.get("understood", ""), decision=raw.get("decision", ""),
                     reply=(raw.get("reply") or "").strip(), ask_owner=raw.get("ask_owner") or None,
                     manager_agreed=bool(raw.get("manager_agreed")),
                     conversation_over=bool(raw.get("conversation_over")))
        up = raw.get("updates") or {}
        from_owner = event == "owner"
        if from_owner:  # times the owner named, or agreed to with "yes", become approved
            answer = next((t.text for t in reversed(self.conv.turns) if t.role == "owner"), "")
            approved = extract_times(answer)
            if not approved and _YES_RE.search(answer) and self.last_question:
                approved = extract_times(self.last_question)
            b.approved_times += [t for t in approved if t not in b.approved_times]

        if up.get("restaurant_name"):
            b.restaurant_name = up["restaurant_name"].strip()
        if up.get("note") and up["note"] not in b.notes:
            b.notes.append(up["note"])

        new_time = norm_time(up.get("time")) or up.get("time")
        if new_time and new_time != b.time:
            flex = int(self.s.owner_facts.get("time_flex_minutes", 0) or 0)
            a, c = _minutes(new_time), _minutes(b.time)
            within = a is not None and c is not None and abs(a - c) <= flex
            if from_owner or within or new_time in b.approved_times:
                if new_time not in b.approved_times:
                    b.approved_times.append(new_time)
                b.time = new_time
            else:
                d.guards.append(f"blocked time change to {new_time}: owner has not approved it")
                d.ask_owner = d.ask_owner or f"The manager suggests {new_time} instead of {b.time}. Is that OK?"
                d.reply = HOLDING

        if up.get("party_size") and up["party_size"] != b.party_size:
            if from_owner:
                b.party_size = int(up["party_size"])
            else:
                d.guards.append(f"blocked party size change to {up['party_size']}")
                d.ask_owner = d.ask_owner or f"The manager asks about {up['party_size']} people instead of {b.party_size}. OK?"
                d.reply = HOLDING

        if up.get("guest_name") and up["guest_name"] != b.guest_name:
            if from_owner:
                b.guest_name = up["guest_name"].strip()
            else:
                d.guards.append("ignored guest_name not coming from the owner")

        # No phone / card / any long number that the owner did not give us.
        known_digits = re.sub(r"\D", "", json.dumps(self.s.owner_facts) + " ".join(
            t.text for t in self.conv.turns if t.role == "owner"))
        for m in re.finditer(r"\+?\d[\d\s\-()]{5,}\d", d.reply):
            digits = re.sub(r"\D", "", m.group())
            if len(digits) >= 6 and digits not in known_digits:
                d.guards.append(f"blocked unknown number {m.group()!r} in reply")
                d.ask_owner = d.ask_owner or "The manager needs a number (phone/card) that I don't have. What should I give?"
                d.reply = HOLDING
                break

        if d.ask_owner and event == "owner_timeout":
            # owner is silent: don't loop on "one moment", be honest with the manager instead
            if d.guards:
                d.reply = ("I'm so sorry, I can't confirm that just yet as I haven't been able to reach "
                           "the guest. I'll come back to you as soon as I can.")
            d.ask_owner = None
            return d
        if d.ask_owner:
            b.status = "waiting_owner"
            self.last_question = d.ask_owner
            if not d.reply:
                d.reply = HOLDING
            return d

        # Confirmation.
        if d.manager_agreed or self.agreed_pending:
            if not b.restaurant_name:
                self.agreed_pending = True
                if "name" not in d.reply.lower():
                    d.guards.append("manager agreed but restaurant name unknown: asking for it")
                    d.reply = (d.reply + " Could I just take the name of the restaurant, "
                               "so I have it right for my confirmation?").strip()
            elif b.status not in ("confirmed", "finished"):
                if not recap_is_complete(d.reply, b):
                    d.guards.append("model reply lacked full recap: appended recap from booking state")
                    d.reply = (d.reply + " " + recap(b)).strip()
                b.status = "confirmed"
                self.agreed_pending = False

        if b.status == "not_started" and d.reply:
            b.status = "requested"
        elif b.status == "waiting_owner" and event != "owner_timeout":
            b.status = "requested"
        if d.conversation_over and b.status == "confirmed":
            b.status = "finished"
        return d
