"""Optional LLM layer: rewrites the rule-based response outline (app/response.py) as a short plain-English
briefing. Uses a key from the environment only (XAI_API_KEY, OPENAI_API_KEY or ANTHROPIC_API_KEY, also read
from backend/.env which is gitignored). No key -> no briefing (the UI shows the rule-based outline only)."""
from __future__ import annotations

import json
import logging
import os

import requests

from . import config

log = logging.getLogger(__name__)

PROMPT = """You are writing a short briefing for oil spill first responders (US Coast Guard / NOAA style).
Rewrite the JSON response outline below as plain English: at most 170 words, 4-7 short bullet points, most
urgent first (time to landfall, where to boom first, which tactics have a window and when, closures).
Use ONLY facts and numbers in the JSON; do not invent places, times, quantities or precedents. Times are hours
after release unless a UTC time is given. When you mention a precedent, name the spill and year.
End with one line: "Planning aid only, not operational guidance."

JSON:
"""


def _env() -> dict:
    env = dict(os.environ)
    p = config.BACKEND_DIR / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def provider() -> tuple[str, str, str] | None:
    """(provider, key, model) from the environment, or None."""
    env = _env()
    model = env.get("OILFLOOD_LLM_MODEL")
    if env.get("XAI_API_KEY"):
        return "xai", env["XAI_API_KEY"], model or "grok-3-mini"
    if env.get("OPENAI_API_KEY"):
        return "openai", env["OPENAI_API_KEY"], model or "gpt-4o-mini"
    if env.get("ANTHROPIC_API_KEY"):
        return "anthropic", env["ANTHROPIC_API_KEY"], model or "claude-haiku-5-5"
    return None


def _compact(outline: dict) -> dict:
    """What the LLM needs (drop the hourly series and long source lists)."""
    o = json.loads(json.dumps(outline))
    o.get("conditions", {}).pop("hourly", None)
    o.pop("sources", None)
    o.pop("limits", None)
    for t in o.get("tactics", {}).values():
        t["windows"] = t["windows"][:3]
    o["priority_shorelines"] = o.get("priority_shorelines", [])[:6]
    return o


def generate(outline: dict, timeout: float = 45) -> dict:
    """{text, provider, model} or {text: None, note}."""
    p = provider()
    if p is None:
        return {"text": None, "note": "No LLM API key configured on the server; showing the rule-based outline only."}
    name, key, model = p
    msg = PROMPT + json.dumps(_compact(outline), separators=(",", ":"))
    try:
        if name == "anthropic":
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=timeout,
                              headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"},
                              json={"model": model, "max_tokens": 600, "messages": [{"role": "user", "content": msg}]})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
        else:
            url = "https://api.x.ai/v1/chat/completions" if name == "xai" else "https://api.openai.com/v1/chat/completions"
            r = requests.post(url, timeout=timeout, headers={"Authorization": f"Bearer {key}"},
                              json={"model": model, "temperature": 0.2,
                                    "messages": [{"role": "user", "content": msg}]})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        log.warning("LLM briefing failed (%s %s): %s", name, model, e)
        return {"text": None, "provider": name, "model": model,
                "note": f"LLM briefing failed ({name}): {type(e).__name__}. Showing the rule-based outline only."}
    return {"text": text.strip() or None, "provider": name, "model": model}
