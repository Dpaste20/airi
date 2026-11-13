"""Tool router.

tools.json drives the label set. Per tool:
  mode "auto": no-arg toolkit method; run it here and inject the result into the prompt.
  mode "hint": only tell the agent which tool is likely needed; the agent calls it itself.
  threshold:   optional per-tool confidence override (default: top-level threshold).
If the decision - server is down or errors, the message passes through untouched.
"""
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

import httpx

D1_URL = os.environ.get("D1_URL", "http://127.0.0.1:8090/v1/systemone")

_cfg = json.loads(Path(__file__).with_name("tools.json").read_text())
TOOLS: dict[str, dict] = _cfg["tools"]
THRESHOLD: float = _cfg.get("threshold", 0.85)
TIMEOUT: float = _cfg.get("timeout", 3)

CRITERIA = {"none": "No tool needed, just chat"} | {k: v["description"] for k, v in TOOLS.items()}


async def decide(message: str) -> Optional[dict[str, Any]]:
    """One forward pass: which tool (or none) does this message need?"""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                D1_URL,
                json={
                    "state": message,
                    "questions": {
                        "tool": {
                            "type": "choice",
                            "instructions": "Which tool does the user's message need?",
                            "criteria": CRITERIA,
                        }
                    },
                },
            )
        r.raise_for_status()
        a = r.json()["answers"]["tool"]
        return {
            "tool": a["choice"],
            "confidence": a["confidence"],
            "probabilities": a["probabilities"],
        }
    except Exception as e:
        print(f"[router] d1 unavailable: {e}")
        return None


async def route(message: str, toolkit: Any) -> tuple[str, Optional[dict[str, Any]]]:
    """Return (message for the agent, decision). Decision is None if dm failed."""
    d = await decide(message)
    if d is None:
        return message, None

    name = d["tool"]
    spec = TOOLS.get(name)
    if spec is None or name == "none":
        print(f"[router] none ({d['confidence']:.1%})")
        return message, d
    if d["confidence"] < spec.get("threshold", THRESHOLD):
        print(f"[router] {name} below threshold ({d['confidence']:.1%}), leaving to agent")
        d["skipped"] = "low_confidence"
        return message, d

    print(f"[router] {name} ({d['confidence']:.1%}) mode={spec.get('mode', 'hint')}")

    if spec.get("mode") == "auto":
        fn = getattr(toolkit, name, None)
        if callable(fn):
            try:
                result = await asyncio.to_thread(fn)
            except Exception as e:
                result = json.dumps({"error": str(e)})
            return f"{message}\n\n[tool result: {name}]\n{result}", d

    return f"{message}\n\n[router hint: this request likely needs the `{name}` tool]", d
