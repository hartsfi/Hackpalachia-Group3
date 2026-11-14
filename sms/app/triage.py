"""Understand a patient's text: need, urgency, place, summary, language.

Brain, chosen with LLM_PROVIDER in .env:
  ollama     (default) a local model through Ollama on this machine; nothing leaves it
  anthropic  Claude through the Anthropic API (needs ANTHROPIC_API_KEY)
  rules      keyword rules only
If the model is off or slow, the keyword rules answer instead, so a text is never stuck.

Safety: emergency keywords are always checked. The model may raise urgency, never lower it.
"""
import json
import logging
import re

import requests

from . import config

log = logging.getLogger("triage")

CATEGORIES = [
    "sick_visit", "checkup", "medication", "transportation", "dental", "food", "housing",
    "mental_health", "insurance", "utilities", "other",
]
URGENCIES = ["low", "medium", "high", "emergency"]

# Checked in code on every text, before anything else. Deliberately broad: a false alarm costs
# one 911 reminder, a miss could cost a life. English and Spanish.
EMERGENCY_WORDS = re.compile(
    r"chest (pain|hurts?|is (hurting|tight)|tight(ness)?|pressure)|heart attack|cardiac|"
    r"can[’']?t breathe|cannot breathe|can'?t catch (my|his|her) breath|not breathing|"
    r"(hard|trouble|struggling) (to )?breath(e|ing)|"
    r"stroke|face (is )?droop|slurred speech|(arm|face|side) (is |went |going )?numb|"
    r"passed out|fainted|unconscious|unresponsive|not responding|won[’']?t wake|seizure|"
    r"choking|anaphyla|"
    r"overdos|took too many( of)?( my| his| her| their)? (pills|meds|medicine)|"
    r"bleeding (a lot|heavily|badly|won[’']?t stop)|severe bleeding|heavy bleeding|won[’']?t stop bleeding|"
    r"throat (is )?(closing|swelling)|can'?t swallow|"
    r"suicid|kill myself|end my life|want to die|hurt myself|"
    r"(don[’']?t|do not|no) see (the|any) point|no reason to live|better off without me|"
    r"what[’']?s the point( of (any of )?(this|it|anything|living|life))?( anymore)?\s*[?.!]*$|"
    r"what[’']?s the point of (living|life|going on)|(don[’']?t|do not) (really )?want to (be here|live|wake up)|"
    r"can[’']?t (go on|do this anymore|take (it|this) anymore)|"
    r"ataque al corazon|ataque al corazón|infarto|no puedo respirar|no respira|derrame|sobredosis|"
    r"convulsion|convulsión|matarme|quitarme la vida",
    re.I,
)
# Chest pain on its own: still 911 first, but with the warning signs and a way to a person if mild.
CHEST_WORDS = re.compile(r"chest (pain|hurts?|is (hurting|tight)|tight(ness)?|pressure)|dolor de pecho", re.I)
CRISIS_WORDS = re.compile(
    r"suicid|kill myself|end my life|want to die|hurt myself|"
    r"(don[’']?t|do not|no) see (the|any) point|no reason to live|better off without me|"
    r"what[’']?s the point( of (any of )?(this|it|anything|living|life))?( anymore)?\s*[?.!]*$|"
    r"what[’']?s the point of (living|life|going on)|(don[’']?t|do not) (really )?want to (be here|live|wake up)|"
    r"can[’']?t (go on|do this anymore|take (it|this) anymore)|"
    r"matarme|quitarme la vida", re.I)

KEYWORDS = {
    "medication": r"\bmeds?\b|medicine|medication|medecine|prescription|perscription|refill|pharmac|farmac|"
                  r"insulin|inhaler|\bpil+e?s?\b|tablets?|"
                  r"(low|running out|ran out|out) (on|of|in) (my|his|her|the)?\s*(pil+e?s|meds|medicine)",
    "transportation": r"\bride\b|transport|\bbus\b|\bcar\b|\bdrive\b|gas money|get to (my|the)",
    "sick_visit": r"fever|cough|\bsick\b|pain|hurts?|rash|flu|cold|sore|vomit|infection|ache",
    "checkup": r"check ?up|physical|annual|routine|screening|blood pressure|vaccin|shot",
    "dental": r"tooth|teeth|dental|dentist",
    "food": r"\bfood\b|hungry|groceries|\bsnap\b|\bwic\b|pantry|\bmeal",
    "housing": r"housing|\brent\b|evict|homeless|shelter",
    "mental_health": r"depress|anxi|stress|lonely|counsel|therap|mental",
    "insurance": r"insurance|medicaid|medicare|\bbill\b|copay|coverage",
    "utilities": r"electric|heat|\bwater\b|utility|utilities|phone bill|internet",
}
SPANISH = re.compile(r"\b(hola|necesito|ayuda|cita|medicina|gracias|por favor|no tengo)\b", re.I)

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "urgency": {"type": "string", "enum": URGENCIES},
        "place": {"type": ["string", "null"]},
        "summary": {"type": "string"},
        "language": {"type": "string"},
    },
    "required": ["category", "urgency", "place", "summary", "language"],
    "additionalProperties": False,
}

PROMPT = """You read text messages sent to a rural health text line in Southeast Ohio.
Return JSON with:
- category: the main need, one of {cats}
  (sick_visit = feeling ill or hurt; checkup = routine care; medication = medicine, pills or refills,
  including misspellings like "pilles", "meds", "perscription")
- urgency: one of {urgs}. emergency = possible danger to life right now (chest pain, trouble
  breathing, stroke signs, heavy bleeding, overdose, suicidal thoughts). high = needs care today.
- place: the town or 5-digit ZIP code the person mentions, exactly as a place name, or null
- summary: one plain sentence (max 140 characters) a community health worker can act on
- language: ISO 639-1 code of the message language
Message:
<<<{text}>>>"""


def _keyword_triage(text: str) -> dict:
    category = next((c for c, rx in KEYWORDS.items() if re.search(rx, text, re.I)), "other")
    urgent = re.search(r"urgent|asap|today|right now|ran out|out of", text, re.I)
    return {"category": category, "urgency": "high" if urgent else "medium", "place": None,
            "summary": text[:140], "language": "es" if SPANISH.search(text) else "en", "source": "rules"}


def _clean(data: dict, text: str, source: str) -> dict:
    place = data.get("place")
    return {
        "category": data.get("category") if data.get("category") in CATEGORIES else "other",
        "urgency": data.get("urgency") if data.get("urgency") in URGENCIES else "medium",
        "place": place.strip() if isinstance(place, str) and place.strip() else None,
        "summary": str(data.get("summary") or text)[:140],
        "language": str(data.get("language") or "en")[:5].lower(),
        "source": source,
    }


def _ollama(text: str) -> dict | None:
    try:
        r = requests.post(f"{config.OLLAMA_URL}/api/generate", timeout=config.LLM_TIMEOUT, json={
            "model": config.OLLAMA_MODEL, "stream": False, "format": SCHEMA,
            "options": {"temperature": 0},
            "prompt": PROMPT.format(cats=CATEGORIES, urgs=URGENCIES, text=text)})
        r.raise_for_status()
        return _clean(json.loads(r.json()["response"]), text, f"ollama:{config.OLLAMA_MODEL}")
    except Exception as e:  # Ollama off, model missing, slow, bad JSON: fall back to rules
        log.warning("Ollama triage failed, using keyword rules: %s", e)
        return None


def _anthropic(text: str) -> dict | None:
    import anthropic

    try:
        client = anthropic.Anthropic(timeout=config.LLM_TIMEOUT, max_retries=1)
        resp = client.messages.create(
            model=config.LLM_MODEL, max_tokens=2000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(cats=CATEGORIES, urgs=URGENCIES, text=text)}],
        )
        if resp.stop_reason == "refusal":
            return None
        raw = next(b.text for b in resp.content if b.type == "text")
        return _clean(json.loads(raw), text, f"anthropic:{config.LLM_MODEL}")
    except (anthropic.APIStatusError, anthropic.APIConnectionError, StopIteration, ValueError) as e:
        log.warning("Claude triage failed, using keyword rules: %s", e)
        return None


def triage(text: str) -> dict:
    result = None
    if config.LLM_PROVIDER == "ollama":
        result = _ollama(text)
    elif config.LLM_PROVIDER == "anthropic":
        result = _anthropic(text)
    rules = _keyword_triage(text)
    result = result or rules
    if result["category"] == "other" and rules["category"] != "other":
        result["category"] = rules["category"]
    # Clear medicine words win: someone low on pills must get pharmacies, whatever the model guessed.
    if rules["category"] == "medication":
        result["category"] = "medication"
    # Emergency keywords always win; the model can raise urgency but nothing here lowers it.
    if EMERGENCY_WORDS.search(text):
        result["urgency"] = "emergency"
    result["crisis"] = bool(CRISIS_WORDS.search(text))
    return result
