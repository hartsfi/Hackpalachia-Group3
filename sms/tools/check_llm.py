"""Checks the LLM brain on real-life messages. Needs the model running (default: Ollama llama3.1:8b).

    uv run python tools/check_llm.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config, services  # noqa: E402
from app.triage import triage  # noqa: E402

# (message, must be emergency?, expected categories (any of), expected town or None)
CASES = [
    ("I have a fever and cough since 3 days, I'm in Nelsonville", False, {"sick_visit"}, "Nelsonville"),
    ("my mom ran out of her insulin and we got no car, near 45764", False, {"medication", "transportation"}, "Nelsonville"),
    ("my chest hurts really bad and my left arm is numb", True, None, None),
    ("need a regular checkup sometime next week in the plains", False, {"checkup"}, "The Plains"),
    ("my dad cant breathe right and his lips look blue", True, None, None),
    ("i took too many of my pills and feel really weird", True, None, None),
    ("tooth has been killing me for a week, live out by glouster", False, {"dental", "sick_visit"}, "Glouster"),
    ("im out near nelsonvile and need my blood pressure meds refilled", False, {"medication"}, "Nelsonville"),
    ("hola necesito una cita para mi hijo en Athens", False, None, "Athens"),
    ("I just feel so alone and I don't see the point anymore", True, None, None),
]

print("brain:", {"ollama": f"ollama ({config.OLLAMA_MODEL})", "anthropic": f"anthropic ({config.LLM_MODEL})"}.get(config.LLM_PROVIDER, "keyword rules only"))
ok = True
for msg, must_emerg, cats, town in CASES:
    t = time.time()
    r = triage(msg)
    took = time.time() - t
    place = services.find_place(msg) or (services.find_place(r["place"]) if r.get("place") else None)
    fails = []
    if must_emerg is True and r["urgency"] != "emergency":
        fails.append("should be EMERGENCY")
    if must_emerg is False and r["urgency"] == "emergency":
        fails.append("false emergency")
    if cats and r["category"] not in cats:
        fails.append(f"category {r['category']}")
    if town and (not place or place["town"] != town):
        fails.append(f"place {place and place['town']}")
    ok &= not fails
    print(f"{'PASS' if not fails else 'FAIL'} {took:4.1f}s {r['urgency']:9} {r['category']:14} "
          f"{(place or {}).get('town', '-'):12} {r['source']:18} | {msg[:48]}" + (f"  <- {', '.join(fails)}" if fails else ""))
print("ALL PASS" if ok else "SOME FAILED")
