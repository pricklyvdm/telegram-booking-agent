# Table-booking agent for Telegram

An agent that books a table for two at 17:00 today at a London restaurant by chatting
with the restaurant manager on Telegram. It writes first, speaks polite British English,
reacts to whatever the manager says, never invents facts, and repeats the confirmed booking.
It understands the manager's voice notes and can answer with a generated British voice.

## How it works

```
Telegram (user account, Telethon)
   │  text / voice note from the manager
   ▼
voice.py  ── voice note → faster-whisper (local) → text
   ▼
core.py   ── waits for a burst of messages, then one decision per turn
   ▼
brain.py  ── LLM proposes {understood, decision, reply, ask_owner, updates, manager_agreed}
             code guards it against the booking state (state.py)
   ▼
reply → text, or edge-tts en-GB voice note with the text as caption
ask_owner → owner.py (terminal or the owner's Telegram), answer goes back into the brain
every turn → logs/<date>_<peer>.decisions.md (+ .jsonl), written by the agent itself
```

**Why a user account and not a bot:** a Bot API bot cannot message a person first, and the
manager never writes first. So the agent runs on a regular Telegram account via Telethon.

**State lives in code, not in the model.** Party size, time and the date (computed from the
`Europe/London` clock) are in `Booking`. The model only proposes updates; code decides:

- a different time / party size / guest name is accepted only after the owner approves it
  (or within `time_flex_minutes` from `owner_facts.json`);
- any phone/card-like number that the owner did not provide is blocked before sending;
- if something is unknown, the agent asks the owner and sends the manager a holding line;
- when the manager agrees, the reply must contain restaurant, date, time and party size,
  otherwise code appends a recap built from the state. If the restaurant name is unknown,
  the agent asks for it first.

**No invented facts.** Personal details come only from `owner_facts.json` (filled by the
owner) or from the owner's live answers. Empty field = unknown = the agent asks.

## Run

Requirements: Python 3.10+, ffmpeg in PATH, and an LLM: the Codex CLI logged in with a
ChatGPT subscription (default, no API key) or any OpenAI-compatible endpoint.

```bash
python -m venv .venv && .venv/Scripts/activate    # or source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                    # TG_API_ID / TG_API_HASH from my.telegram.org
cp owner_facts.example.json owner_facts.json      # guest name etc., or leave empty

python run.py login                     # phone, code, 2FA, once; creates booking_agent.session
python run.py simulate --script scenarios/02_alt_time_deposit.txt   # offline, no Telegram
python run.py run --to @my_test_account             # test with a second account
python run.py run --to @vlad_dev_7 --live           # the real manager, on purpose only
```

When the agent needs the owner, the question appears in the terminal (`OWNER_CHANNEL=console`)
or in the owner's Telegram (`OWNER_CHANNEL=@username` or `me` for Saved Messages).

Voice: `VOICE_MODE=mirror` answers a voice note with a voice note (`always` for every
message, `off` to disable). Each voice note carries its text as a caption, so the chat
stays readable and the confirmation is always visible as text.

## Files

| File | What |
|---|---|
| `run.py` | CLI: `login`, `run`, `simulate` |
| `booking_agent/brain.py` | prompt assembly, LLM call, code guards |
| `booking_agent/prompts.py` | system prompt (rules of behaviour) |
| `booking_agent/state.py` | booking state, transcript, London date |
| `booking_agent/llm.py` | `codex exec` and OpenAI-compatible backends, JSON schema |
| `booking_agent/voice.py` | Whisper (in), edge-tts + ffmpeg (out) |
| `booking_agent/owner.py` | how the agent asks the owner |
| `booking_agent/core.py` | turn loop shared by Telegram and the simulator |
| `booking_agent/telegram_runner.py` | Telethon client and handlers |
| `scenarios/` | scripted manager behaviours for offline tests |
