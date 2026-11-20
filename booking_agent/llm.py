"""LLM backends returning one JSON object that matches DECISION_SCHEMA.

codex  - `codex exec` CLI on a ChatGPT subscription (no API key needed).
openai - any OpenAI-compatible /chat/completions endpoint (OpenAI, LM Studio, OpenRouter...).
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import urllib.request
import uuid
from pathlib import Path

from .config import Settings

_NULLABLE_STR = {"type": ["string", "null"]}

DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["understood", "decision", "reply", "ask_owner", "updates", "offered_time",
                 "manager_agreed", "manager_cancelled", "conversation_over"],
    "properties": {
        "understood": {"type": "string", "description": "What the manager said/means, one short line."},
        "decision": {"type": "string", "description": "What you decided to do and why, one short line."},
        "reply": {"type": "string", "description": "Message to send to the manager, or empty string."},
        "ask_owner": {**_NULLABLE_STR, "description": "Question for the owner when a fact is unknown, else null."},
        "updates": {
            "type": "object",
            "additionalProperties": False,
            "required": ["restaurant_name", "time", "party_size", "guest_name", "note"],
            "properties": {
                "restaurant_name": _NULLABLE_STR,
                "time": _NULLABLE_STR,
                "party_size": {"type": ["integer", "null"]},
                "guest_name": _NULLABLE_STR,
                "note": _NULLABLE_STR,
            },
        },
        "offered_time": {**_NULLABLE_STR,
                         "description": "A different time the manager offered in the NEW message(s), HH:MM, else null."},
        "manager_agreed": {"type": "boolean"},
        "manager_cancelled": {"type": "boolean",
                              "description": "The manager cancelled or withdrew an already confirmed booking."},
        "conversation_over": {"type": "boolean"},
    },
}


class LLMError(RuntimeError):
    pass


def _parse(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise LLMError(f"no JSON in model output: {text[:200]!r}")
    return json.loads(text[start:end + 1])


async def complete(settings: Settings, system: str, user: str) -> dict:
    if settings.llm_backend == "openai":
        return await asyncio.to_thread(_openai, settings, system, user)
    return await _codex(settings, system, user)


# Codex is a coding agent with tools. The booking brain needs none of them, and the manager's
# text is untrusted input, so every tool that can touch the machine or the network is switched
# off (verified: no shell, no file access). What Codex cannot switch off (sub-agent spawn,
# a JS sandbox without files or network) is caught by a watchdog that kills the call.
CODEX_DISABLED_FEATURES = [
    "shell_tool", "shell_snapshot", "apps", "plugins", "browser_use", "browser_use_external",
    "computer_use", "in_app_browser", "image_generation", "view_image", "multi_agent",
    "multi_agent_v2", "hooks", "memories", "goals", "tool_suggest", "sleep_tool", "code_mode",
]
ALLOWED_CODEX_ITEMS = {"agent_message", "reasoning", "error"}


async def _codex(settings: Settings, system: str, user: str) -> dict:
    # Empty temp dir: no project AGENTS.md, no repo, nothing to read.
    work = Path(tempfile.gettempdir()) / "booking-agent-codex"
    work.mkdir(exist_ok=True)
    schema = work / "decision.schema.json"
    schema.write_text(json.dumps(DECISION_SCHEMA), encoding="utf-8")
    out = work / f"last_{uuid.uuid4().hex}.json"

    args = [settings.codex_bin, "exec", "--json", "--skip-git-repo-check", "--ephemeral",
            "--ignore-user-config",  # no user MCP servers, hooks or plugins
            "--ignore-rules", "--sandbox", "read-only", "--color", "never",
            "-m", settings.codex_model or "gpt-6.2-sol",
            "-c", f'model_reasoning_effort="{settings.codex_reasoning}"',
            "-c", 'web_search="disabled"',
            "-C", str(work), "--output-schema", str(schema), "-o", str(out)]
    for feature in CODEX_DISABLED_FEATURES:
        args += ["--disable", feature]
    args.append("-")  # prompt from stdin

    prompt = (f"{system}\n\n{user}\n\nYou have no tools and need none. "
              "Answer immediately with the JSON object only.")
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    # Watchdog: the brain must only produce a message. Any tool activity (sub-agent spawn,
    # command, file change, MCP call...) kills the process at once.
    tool_used: list[str] = []
    err_tail: list[str] = []

    async def watch_stdout():
        async for raw in proc.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            kind = (ev.get("item") or {}).get("type")
            if kind and kind not in ALLOWED_CODEX_ITEMS:
                tool_used.append(kind)
                proc.kill()

    async def watch_stderr():
        async for raw in proc.stderr:
            line = raw.decode("utf-8", "ignore")
            err_tail[:] = (err_tail + [line])[-5:]
            if "tools::router" in line or "collab" in line:
                tool_used.append(line.strip()[:120])
                proc.kill()

    try:
        await asyncio.wait_for(asyncio.gather(watch_stdout(), watch_stderr(), proc.wait()),
                               timeout=settings.llm_timeout)
    except asyncio.TimeoutError:  # hard cap on how long (and how much) one call may run
        proc.kill()
        raise LLMError(f"codex timed out after {settings.llm_timeout}s")
    if tool_used:
        out.unlink(missing_ok=True)
        raise LLMError(f"model tried to use a tool ({tool_used[0]}): call killed")
    if not out.exists():
        raise LLMError(f"codex failed ({proc.returncode}): {''.join(err_tail)[-500:]}")
    try:
        return _parse(out.read_text(encoding="utf-8"))
    finally:
        out.unlink(missing_ok=True)


def _openai(settings: Settings, system: str, user: str) -> dict:
    body = {
        "model": settings.openai_model,
        "temperature": 0.4,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "decision", "strict": True, "schema": DECISION_SCHEMA}},
    }
    req = urllib.request.Request(
        settings.openai_base.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings.openai_key}"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return _parse(data["choices"][0]["message"]["content"])
