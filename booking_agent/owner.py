"""The owner (the busy person the table is for).

In the default autonomous mode the agent never waits for the owner: it acts within
owner_profile.json and only sends the owner a short report (booking confirmed / stopped).
In "ask" mode the agent can also ask the owner a question and wait for the answer.

none      - no owner channel at all.
console   - the terminal where the agent runs.
telegram  - the owner's Telegram account (@username), or 'me' = the agent account's
            Saved Messages; in ask mode the owner's next message there is the answer.
script    - pre-written answers, for offline tests.
"""
from __future__ import annotations

import asyncio


class NoOwner:
    async def ask(self, question: str, timeout: int) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        pass


class ConsoleOwner:
    async def ask(self, question: str, timeout: int) -> str | None:
        print(f"\a\n>>> OWNER, the agent needs you: {question}")
        try:
            ans = await asyncio.wait_for(asyncio.to_thread(input, ">>> your answer: "), timeout)
        except asyncio.TimeoutError:
            print(">>> (no answer, timed out)")
            return None
        return ans.strip() or None

    async def notify(self, text: str) -> None:
        print(f">>> REPORT TO OWNER: {text}")


class ScriptOwner(ConsoleOwner):
    def __init__(self, answers: list[str]):
        self.answers = list(answers)

    async def ask(self, question: str, timeout: int) -> str | None:
        ans = self.answers.pop(0) if self.answers else None
        print(f">>> OWNER asked: {question}\n>>> OWNER (script): {ans}")
        return ans


class TelegramOwner:
    """Owner in Telegram. The runner calls .feed() for each new message in the owner chat."""

    def __init__(self, client, peer):
        self.client = client
        self.peer = peer
        self.waiting: asyncio.Future | None = None
        self.sent_ids: set[int] = set()

    async def _send(self, text: str) -> None:
        msg = await self.client.send_message(self.peer, text)
        self.sent_ids.add(msg.id)

    async def ask(self, question: str, timeout: int) -> str | None:
        self.waiting = asyncio.get_running_loop().create_future()
        await self._send(f"Booking agent needs you: {question}\n(reply here, I will pass it on)")
        try:
            return await asyncio.wait_for(self.waiting, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self.waiting = None

    async def notify(self, text: str) -> None:
        try:
            await self._send(f"Booking agent: {text}")
        except Exception:
            pass

    def feed(self, msg_id: int, text: str) -> None:
        if msg_id in self.sent_ids or not text:
            return
        if self.waiting and not self.waiting.done():
            self.waiting.set_result(text.strip()[:500])
