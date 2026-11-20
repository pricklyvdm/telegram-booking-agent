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
outside the owner's limits → rewrite once, else a safe decline; booked → report to the owner
every turn → logs/<date>_<peer>.decisions.md (+ .jsonl), written by the agent itself
```

**Why a user account and not a bot:** a Bot API bot cannot message a person first, and the
manager never writes first. So the agent runs on a regular Telegram account via Telethon.

**State lives in code, not in the model.** Party size, time and the date (computed from the
`Europe/London` clock) are in `Booking`. The model only proposes updates; code decides:

- a different time / party size is accepted only inside the owner's profile
  (`time_flex_minutes` in `owner_profile.json`); the guest name only from the profile;
- any phone/card-like number that the owner did not provide is blocked before sending;
- a draft that breaks the owner's limits is rewritten once, then replaced by a safe decline;
- when the manager agrees, the reply must contain restaurant, date, time and party size,
  otherwise code appends a recap built from the state. If the restaurant name is unknown,
  the agent asks for it first.

**No invented facts, no babysitting.** The agent works for a busy person who delegated the
booking. Everything it may say or accept comes from `owner_profile.json`, filled once:
the name for the booking, the acceptable time window (17:00 ± 30 min), the deposit policy,
whether a phone may be shared. Inside these limits it decides on its own; outside them it
declines politely and asks for an alternative; anything it does not know it says it does not
have, instead of making it up. The owner only gets a short report in Telegram when the table
is booked. (`OWNER_MODE=ask` is an alternative where the agent asks the owner and waits.)

## Security

The manager's messages are untrusted input. Defences are in code, not only in the prompt:

- the LLM call has no tools that touch the machine or the network: Codex runs with shell,
  browser, computer use, apps, plugins, MCP servers, web search, image tools and hooks
  disabled, in an empty directory, read-only sandbox, with a hard timeout (verified: it cannot
  run commands or read files). Codex still exposes sub-agents and a JS sandbox without file or
  network access; a watchdog kills the call on the first sign of any tool use. It reacts to an
  observed event, so it limits the damage rather than proving nothing started. For strict
  isolation use `LLM_BACKEND=openai` (a plain API call with no tools at all; checked with a
  local model behind LM Studio's OpenAI-compatible server);
- one LLM call per burst of messages, max `MAX_LLM_CALLS` per conversation (counted per
  process run: a `--resume` starts the count again), incoming text cut
  to `MAX_INCOMING_CHARS`, voice notes longer than `MAX_VOICE_SECONDS` are not transcribed,
  only the last `HISTORY_TURNS` messages go to the model;
- outgoing guard: replies containing a secret value from `.env`, a file path or a key-like
  string are blocked; phone/card numbers not in the profile are blocked; length is capped;
- booking changes outside the profile are blocked by code even if the model agrees
  (a "message from the owner" typed by the manager has no authority);
- secrets only in `.env` and `*.session`, both git-ignored; writing to the real manager
  requires `--live`, checked before connecting.

## Run

Requirements: Python 3.10+, ffmpeg in PATH, and an LLM: the Codex CLI logged in with a
ChatGPT subscription (default, no API key) or any OpenAI-compatible endpoint.

```bash
python -m venv .venv && .venv/Scripts/activate    # or source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                    # TG_API_ID / TG_API_HASH from my.telegram.org
cp owner_profile.example.json owner_profile.json  # name, time window, deposit policy

python run.py login                     # phone, code, 2FA, once; creates booking_agent.session
python run.py simulate --script scenarios/02_alt_time_deposit.txt   # offline, no Telegram
python run.py run --to @my_test_account             # test with a second account
python run.py run --to @vlad_dev_7 --live           # the real manager, on purpose only
```

The owner's report goes to `OWNER_CHANNEL`: `me` (the agent account's Saved Messages),
`@username`, `console` or `none`. The terminal only shows the running process and its log.

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

## Tests and decisions

- `python tests/test_live_races.py`: no-network checks with a fake model for two live-chat
  races (the manager corrects themselves while the model is thinking; the manager withdraws
  the agreement in the same message that names the restaurant).
- `python run.py simulate --script scenarios/<file>.txt`: scripted manager behaviours
  (alternative time, deposit, name, phone, voice notes, prompt injection, offers outside the
  owner's limits, silence, language switch, cancellation).
- [DECISIONS.md](DECISIONS.md) (Ukrainian): every open question in the brief, the decision
  taken and why.
