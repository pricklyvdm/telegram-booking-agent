"""How the agent gets facts it does not know: it asks the owner (the real guest).

console   - question appears in the terminal where the agent runs, owner types the answer.
telegram  - question is sent to the owner's Telegram (or to Saved Messages, 'me'),
            the owner's next message there is the answer.
script    - pre-written answers, for offline tests.
"""
from __future__ import annotations

import asyncio


class ConsoleOwner:
    async def ask(self, question: str, timeout: int) -> str | None:
        print(f"\a\n>>> OWNER, the agent needs you: {question}")
        try:
            ans = await asyncio.wait_for(asyncio.to_thread(input, ">>> your answer: "), timeout)
        except asyncio.TimeoutError:
            print(">>> (no answer, timed out)")
            return None
        return ans.strip() or None


class ScriptOwner:
    def __init__(self, answers: list[str]):
        self.answers = list(answers)

    async def ask(self, question: str, timeout: int) -> str | None:
        ans = self.answers.pop(0) if self.answers else None
        print(f">>> OWNER asked: {question}\n>>> OWNER (script): {ans}")
        return ans


class TelegramOwner:
    """Owner answers in Telegram. The runner calls .feed(text) for each owner message."""

    def __init__(self, client, peer):
        self.client = client
        self.peer = peer
        self.waiting: asyncio.Future | None = None
        self.sent_ids: set[int] = set()

    async def ask(self, question: str, timeout: int) -> str | None:
        self.waiting = asyncio.get_running_loop().create_future()
        msg = await self.client.send_message(
            self.peer, f"Booking agent needs you: {question}\n(reply here, I will pass it on)")
        self.sent_ids.add(msg.id)
        try:
            return await asyncio.wait_for(self.waiting, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self.waiting = None

    def feed(self, msg_id: int, text: str) -> None:
        if msg_id in self.sent_ids or not text:
            return
        if self.waiting and not self.waiting.done():
            self.waiting.set_result(text.strip())
