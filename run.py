"""Entry point.

python run.py login                         first Telegram login (phone, code, 2FA in this terminal)
python run.py run --to @tester              start the conversation with a test account
python run.py run --to @vlad_dev_7 --live   the real manager (needs --live on purpose)
python run.py simulate [--script FILE]      offline, no Telegram
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from booking_agent.config import Settings  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Telegram agent that books a table")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login")
    r = sub.add_parser("run")
    r.add_argument("--to", required=True, help="@username of the manager (or a test account)")
    r.add_argument("--live", action="store_true", help="allow writing to the protected real manager")
    r.add_argument("--resume", action="store_true", help="continue today's saved conversation")
    r.add_argument("--force", action="store_true", help="run even if 17:00 London has passed")
    sm = sub.add_parser("simulate")
    sm.add_argument("--script", type=Path)
    a = p.parse_args()

    s = Settings()
    if a.cmd == "simulate":
        from booking_agent.simulate import simulate
        asyncio.run(simulate(s, a.script))
        return
    from booking_agent import telegram_runner
    if a.cmd == "login":
        asyncio.run(telegram_runner.login(s))
    else:
        try:
            asyncio.run(telegram_runner.run(s, a.to, a.live, a.resume, a.force))
        except KeyboardInterrupt:
            print("Stopped.")


if __name__ == "__main__":
    main()
