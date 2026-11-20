"""The agent writes its own decision log: one line per message it handles.

logs/<run>.decisions.md   - human-readable table (this is what we hand in)
logs/<run>.decisions.jsonl - same data, machine-readable
"""
from __future__ import annotations

import json
from pathlib import Path

from .state import london_now


def _cell(s: str | None) -> str:
    return (s or "").replace("|", "/").replace("\n", " ").strip()


class DecisionLog:
    def __init__(self, logs_dir: Path, run_name: str, peer: str):
        self.md = logs_dir / f"{run_name}.decisions.md"
        self.jsonl = logs_dir / f"{run_name}.decisions.jsonl"
        if not self.md.exists():
            self.md.write_text(
                f"# Decision log: {peer}, {london_now().strftime('%d %B %Y')}\n\n"
                "Times are Europe/London. Written by the agent itself.\n\n"
                "| Time | Received | Understood | Decided | Sent |\n"
                "|---|---|---|---|---|\n", encoding="utf-8")

    def write(self, received: str, understood: str, decided: str, sent: str, **extra) -> None:
        ts = london_now().strftime("%H:%M:%S")
        with self.md.open("a", encoding="utf-8") as f:
            f.write(f"| {ts} | {_cell(received)} | {_cell(understood)} | {_cell(decided)} | {_cell(sent)} |\n")
        rec = {"ts": london_now().isoformat(timespec="seconds"), "received": received,
               "understood": understood, "decided": decided, "sent": sent, **extra}
        with self.jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
