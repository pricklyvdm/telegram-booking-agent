"""Race and withdrawal checks with a fake model (no network): python tests/test_live_races.py"""
import asyncio, contextlib, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")
from booking_agent import llm, core
from booking_agent.config import Settings
from booking_agent.decision_log import DecisionLog
from booking_agent.owner import ConsoleOwner
from booking_agent.state import Booking, Conversation

BASE = {"understood": "", "decision": "", "ask_owner": None, "offered_time": None,
        "updates": {"restaurant_name": None, "time": None, "party_size": None, "guest_name": None, "note": None},
        "manager_cancelled": False, "conversation_over": False}


class T:
    sent = []
    async def send_text(self, text): T.sent.append(text)
    async def send_voice(self, *a, **k): pass
    def typing(self, kind="typing"): return contextlib.nullcontext()


class Owner(ConsoleOwner):
    notes = []
    async def notify(self, text): Owner.notes.append(text)


def make(s, name):
    conv = Conversation("@t", Booking.for_today(s.owner_facts))
    return core.BookingAgent(s, conv, T(), Owner(), DecisionLog(s.logs_dir, name, "@t"),
                             s.logs_dir / f"{name}.json", echo=lambda *a: None)


async def test_correction_while_thinking(s):
    T.sent.clear(); Owner.notes.clear()
    a = make(s, "crit1")
    calls = []

    async def fake(settings, system, user):
        calls.append(user)
        if "already taken" in user:
            return {**BASE, "reply": "That's a shame. Is there anything else close to 17:00?", "manager_agreed": False}
        await asyncio.sleep(0.5)  # meanwhile the correction arrives
        return {**BASE, "reply": "Lovely, booked then.", "manager_agreed": True,
                "updates": {**BASE["updates"], "restaurant_name": "Demo Bistro"}}
    llm.complete = fake
    a.conv.add("agent", "Could I book a table for two at 17:00 today?")
    a.conv.processed_upto = len(a.conv.turns)
    a.on_manager_message("Yes, 17:00 at Demo Bistro is free.")

    async def later():
        await asyncio.sleep(0.2)
        a.on_manager_message("Sorry, it's already taken.")
    await asyncio.gather(a.process("manager"), later())
    ok = T.sent == ["That's a shame. Is there anything else close to 17:00?"] and a.conv.booking.status != "confirmed" \
        and not any("booked" in n for n in Owner.notes)
    print("C1 correction while thinking:", "PASS" if ok else "FAIL", T.sent, a.conv.booking.status, Owner.notes)


async def test_withdraw_with_name(s):
    T.sent.clear(); Owner.notes.clear()
    a = make(s, "crit2")
    step = {"n": 0}

    async def fake(settings, system, user):
        step["n"] += 1
        if step["n"] == 1:
            return {**BASE, "reply": "Thank you! Could I take the restaurant's name and address for the calendar?",
                    "manager_agreed": True}
        return {**BASE, "reply": "Oh dear. Is anything else free between 16:30 and 17:30?", "manager_agreed": False,
                "manager_cancelled": True, "updates": {**BASE["updates"], "restaurant_name": "Demo Bistro"}}
    llm.complete = fake
    a.conv.add("agent", "Could I book a table for two at 17:00 today?")
    a.conv.processed_upto = len(a.conv.turns)
    a.on_manager_message("Booked, two at 17:00.")
    await a.process("manager")
    a.on_manager_message("It's Demo Bistro, but sorry, the table has just gone.")
    await a.process("manager")
    ok = a.conv.booking.status != "confirmed" and not any("booked" in n for n in Owner.notes) \
        and not any("Just to confirm" in m for m in T.sent)
    print("C2 withdraw with name:", "PASS" if ok else "FAIL", T.sent, a.conv.booking.status, Owner.notes)


async def main():
    s = Settings()
    await test_correction_while_thinking(s)
    await test_withdraw_with_name(s)

asyncio.run(main())
