"""Routing between patients and community health workers (CHWs), plus booking by text.

Patient (any carrier) --text--> clinic number --> server --> on-call CHW phones
CHW texts "#<case> message"      --> server --> patient, from the clinic number

Patient booking conversation (case.stage):
  ask_where   what care do you need, and your ZIP or town?
  ask_income  1 = low income / Medicaid / no insurance, 2 = other insurance or can pay
  ask_slot    up to three numbered visits (name, distance, next time); reply a number
  ask_identity  athenahealth clinics only, once per phone: name + date of birth for the patient record
  booked / done / None   free texting with the health worker
Replies in the patient's own words ("im on medicaid", "the tuesday one") are mapped to the exact
answer by understand.py when Claude is on; the questions, replies and bookings stay scripted.
Emergencies skip all of this: 911 / 988 first, CHWs alerted, no booking.
Every text to a patient is kept within 160 GSM-7 characters (no links).
"""
import datetime as dt
import json
import logging
import re

from . import athena, config, db, services, sms, understand
from .triage import CRISIS_WORDS, EMERGENCY_WORDS, triage

log = logging.getLogger("relay")

# Carrier/Twilio opt-out and opt-in keywords (whole message). Note: a bare CANCEL is an
# opt-out on Twilio, which is why changing a visit uses "CANCEL VISIT".
STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "revoke", "optout"}
START_WORDS = {"start", "unstop"}
CANCEL_VISIT = {"cancel visit", "cancel appointment", "cancel booking", "cancelar cita"}
RESCHEDULE = {"reschedule", "change visit", "cambiar cita"}
CHW_REPLY = re.compile(r"^\s*#(\d+)\s*(.*)$", re.S)
YES, NO = {"1", "yes", "y", "si", "sí"}, {"2", "no", "n"}

TEXT = {
    "en": {
        "start": "CareLine: What do you need care for, and what is your ZIP code or town? "
                 "If this is an emergency, call 911.",
        "where_again": "Sorry, I could not find that place. Please send a 5-digit ZIP code or "
                       "town name, like 45701 or Nelsonville.",
        "place_ok": "Got it: {town}, OH {zip}. Reply 1 if low income, Medicaid or no insurance. "
                    "Reply 2 if other insurance or you can pay.",
        "income_again": "Please reply 1 (low income, Medicaid or no insurance) or 2 (other "
                        "insurance or can pay).",
        "options": "Reply 1-{n} to book (0 = none):",
        "slot_again": "Reply a number from 1 to {n} to book, or 0 for none of these.",
        "none_found": "No open times near you right now. A health worker will text you to set "
                      "one up.",
        "declined": "OK. A health worker will text you here to find a time that works.",
        "taken": "Sorry, that time was just taken. Other times at {short}, reply 1-{n} (0 = none):",
        "booked": "Booked: {short}, {when}, {address}, {town}. Call {phone}. Text CANCEL VISIT "
                  "or RESCHEDULE to change.",
        "moved": "Moved: {short}, {when}, {address}, {town}. Call {phone}. Old time cancelled.",
        "cancelled": "Your visit {when} at {short} is cancelled. Text us anytime to book again.",
        "resched": "Pick a new time. Your visit {when} stays booked until you choose.",
        "kept": "OK, your visit {when} at {short} is still booked.",
        "no_booking": "You have no upcoming visit. Text what care you need and your ZIP to book.",
        "reminder": "Reminder: visit {when} at {short}, {address}, {town}. Call {phone}. Text "
                    "CANCEL VISIT or RESCHEDULE to change.",
        "emergency": "If someone is in danger right now, call 911. We are alerting a health "
                     "worker now.",
        "crisis": "You are not alone. Call or text 988 any time to reach the Suicide & Crisis "
                  "Lifeline. If you are in danger now, call 911.",
        "help": "CareLine health line. Text what care you need and your ZIP to book. CANCEL "
                "VISIT or RESCHEDULE changes a visit. STOP to opt out. Emergency: 911",
        "resubscribed": "You will get CareLine texts again. Text what care you need and your "
                        "ZIP code or town.",
        "welcome": "CareLine health line: replies to your texts, visit bookings and reminders. "
                   "Msg & data rates may apply. Reply HELP for help, STOP to opt out.",
        "call_info": "Mobile clinic {when}, {where}, {town}. Free, ages 18-64. Call {phone} to book, "
                     "or reply 1 and we will call for you.",
        "callhelp_ok": "OK. A health worker will call the mobile clinic to book for you and text you here.",
        "mobile_list": "Free mobile clinic, ages 18-64. Call {phone} to book:",
        "mobile_none": "No mobile clinic stops are scheduled right now. Call {phone} for the next dates.",
        "ask_name": "To set up your CareLine account, reply with your first and last name (or SKIP).",
        "name_again": "Please reply with just your first and last name, like Jane Doe, or SKIP.",
        "account": "Thanks {first}! Your CareLine ID is {code}. Keep it for your visits.",
        "account_noname": "OK. Your CareLine ID is {code}. Keep it for your visits.",
        "welcome_back": "Welcome back {first}! What do you need care for, and what is your ZIP code or "
                        "town? If this is an emergency, call 911.",
        "my_id": "Your CareLine ID is {code}{name}.",
        "identity_dob": "To book at {short}, reply with your date of birth, like 03/14/1980",
        "identity": "To book at {short}, reply with your first and last name and date of birth, "
                    "like: Jane Doe 03/14/1980",
        "identity_again": "Please send first name, last name and date of birth as MM/DD/YYYY, like: "
                          "Jane Doe 03/14/1980",
        "system_down": "We could not reach the clinic's booking system. A health worker will text "
                       "you to finish this.",
    },
    # Spanish avoids á í ó ú so every text stays in the 160-character GSM-7 alphabet.
    "es": {
        "start": "CareLine: ¿Que atencion medica necesita, y cual es su codigo postal o "
                 "pueblo? Si es una emergencia, llame al 911.",
        "where_again": "No encontré ese lugar. Envie un codigo postal de 5 digitos o el nombre "
                       "del pueblo, como 45701 o Nelsonville.",
        "place_ok": "Listo: {town}, OH {zip}. Responda 1 si tiene bajos ingresos, Medicaid o no "
                    "tiene seguro. Responda 2 si tiene otro seguro o puede pagar.",
        "income_again": "Responda 1 (bajos ingresos, Medicaid o sin seguro) o 2 (otro seguro o "
                        "puede pagar).",
        "options": "Responda 1-{n} para reservar (0 = ninguna):",
        "slot_again": "Responda un numero del 1 al {n} para reservar, o 0 para ninguna.",
        "none_found": "No hay horas libres cerca ahora. Un trabajador de salud le escribira "
                      "para buscar una.",
        "declined": "Bien. Un trabajador de salud le escribira aqui para buscar una hora.",
        "taken": "Esa hora ya se tomo. Otras horas en {short}, responda 1-{n} (0 = ninguna):",
        "booked": "Reservado: {short}, {when}, {address}, {town}. Tel {phone}. CANCELAR CITA o "
                  "CAMBIAR CITA para cambiar.",
        "moved": "Cambiado: {short}, {when}, {address}, {town}. Tel {phone}. La hora anterior "
                 "se cancelo.",
        "cancelled": "Su cita {when} en {short} esta cancelada. Escribanos para reservar otra.",
        "resched": "Elija una nueva hora. Su cita {when} sigue reservada hasta que elija.",
        "kept": "Bien, su cita {when} en {short} sigue reservada.",
        "no_booking": "No tiene citas. Escriba que atencion necesita y su codigo postal.",
        "reminder": "Recordatorio: cita {when} en {short}, {address}, {town}. Tel {phone}. "
                    "CANCELAR CITA o CAMBIAR CITA para cambiar.",
        "emergency": "Si alguien esta en peligro ahora, llame al 911. Estamos avisando a un "
                     "trabajador de salud.",
        "crisis": "No esta solo. Llame o escriba al 988 (oprima 2 para español). Si esta en "
                  "peligro ahora, llame al 911.",
        "help": "CareLine: escriba que atencion necesita y su codigo postal. CANCELAR CITA o "
                "CAMBIAR CITA. STOP para salir. Emergencia: 911",
        "resubscribed": "Recibira mensajes de CareLine otra vez. Escriba que atencion "
                        "necesita y su codigo postal.",
        "welcome": "Linea de salud CareLine: respuestas, citas y recordatorios. Pueden aplicar "
                   "tarifas de mensajes y datos. HELP para ayuda, STOP para salir.",
        "call_info": "Clinica movil {when}, {where}, {town}. Gratis, 18-64 años. Llame al {phone} para "
                     "reservar, o responda 1 y le llamamos.",
        "callhelp_ok": "Bien. Un trabajador de salud llamara a la clinica movil para reservar y le escribira aqui.",
        "mobile_list": "Clinica movil gratis, 18-64 años. Llame al {phone}:",
        "mobile_none": "No hay paradas de la clinica movil ahora. Llame al {phone} para las proximas fechas.",
        "ask_name": "Para crear su cuenta de CareLine, responda con su nombre y apellido (o SKIP).",
        "name_again": "Responda solo con su nombre y apellido, como Ana Diaz, o SKIP.",
        "account": "Gracias {first}! Su numero de CareLine es {code}. Guardelo para sus citas.",
        "account_noname": "Listo. Su numero de CareLine es {code}. Guardelo para sus citas.",
        "welcome_back": "Bienvenido otra vez {first}! ¿Que atencion necesita y cual es su codigo postal o "
                        "pueblo? Emergencia: 911.",
        "my_id": "Su numero de CareLine es {code}{name}.",
        "identity_dob": "Para reservar en {short}, responda con su fecha de nacimiento, asi: 03/14/1980",
        "identity": "Para reservar en {short}, responda con su nombre, apellido y fecha de "
                    "nacimiento, asi: Ana Diaz 03/14/1980",
        "identity_again": "Envie nombre, apellido y fecha de nacimiento como MM/DD/AAAA, asi: Ana "
                          "Diaz 03/14/1980",
        "system_down": "No pudimos conectar con el sistema de citas de la clinica. Un trabajador "
                       "de salud le escribira para terminar.",
    },
}


def _t(lang: str | None, key: str, **kw) -> str:
    return TEXT.get(lang if lang in TEXT else "en", TEXT["en"])[key].format(**kw)


def _tail(phone: str) -> str:
    return "..." + phone[-4:]


def is_chw(phone: str) -> bool:
    return phone in config.CHW_NUMBERS


def _notify_chws(case, text: str) -> None:
    targets = [case["assigned_chw"]] if case["assigned_chw"] else config.CHW_NUMBERS
    for chw in targets:
        sms.send(chw, text, case["id"], "system")


def handle_incoming(from_phone: str, body: str) -> None:
    body = (body or "").strip()
    if not body:
        return
    if is_chw(from_phone):
        _from_chw(from_phone, body)
    else:
        _from_patient(from_phone, body)


# ---------------------------------------------------------------- patients
def _from_patient(phone: str, body: str) -> None:
    word = " ".join(body.lower().split()).strip(".!")
    case = db.open_case_for(phone)
    case_id = case["id"] if case else None

    # Opt out / back in. Twilio also enforces these and sends its own confirmations.
    if word in STOP_WORDS:
        db.add_message(case_id, "patient", phone, config.CLINIC_NUMBER, body, "inbound")
        db.opt_out(phone)
        if case:
            db.update_case(case_id, status="closed", stage=None)
        return
    if word in START_WORDS:
        db.opt_in(phone)
        db.add_message(case_id, "patient", phone, config.CLINIC_NUMBER, body, "inbound")
        sms.send(phone, _t(case["language"] if case else "en", "resubscribed"), case_id, "system")
        return
    if db.is_opted_out(phone):
        db.add_message(case_id, "patient", phone, config.CLINIC_NUMBER, body, "inbound")
        return

    # Read free text with the LLM brain once; menu answers ("1", "0", keywords) skip it.
    tri = triage(body) if _is_free_text(word) else None

    # Emergencies first, at any point in the conversation: no booking flow.
    if EMERGENCY_WORDS.search(body) or (tri and tri["urgency"] == "emergency"):
        if case is None:
            tri = tri or triage(body)
            case_id = db.create_case(phone, tri)
            case = db.get_case(case_id)
        db.add_message(case_id, "patient", phone, config.CLINIC_NUMBER, body, "inbound")
        db.update_case(case_id, urgency="emergency", stage=None)
        lang = case["language"]
        if CRISIS_WORDS.search(body):
            sms.send(phone, _t(lang, "crisis"), case_id, "system")
        else:
            sms.send(phone, _t(lang, "emergency"), case_id, "system")
        _notify_chws(case, f"[#{case_id} EMERGENCY] {_tail(phone)}: {body}\nReply: #{case_id} your message")
        return

    if word == "help":
        db.add_message(case_id, "patient", phone, config.CLINIC_NUMBER, body, "inbound")
        sms.send(phone, _t(case["language"] if case else "en", "help"), case_id, "system")
        return

    if word in ("mobile", "mobile clinic", "movil", "clinica movil"):
        _send_mobile_route(phone, case, body)
        return

    if word in ("my id", "id"):
        _send_my_id(phone, case)
        return

    if word in CANCEL_VISIT or word in RESCHEDULE:
        _change_visit(phone, body, word, case)
        return

    if case is None:
        _new_case(phone, body, tri or triage(body))
        return

    db.add_message(case_id, "patient", phone, config.CLINIC_NUMBER, body, "inbound")
    stage = case["stage"]
    # "im on medicaid", "the tuesday one": turned into the exact answer the question expects.
    body, word = understand.answer(case, body, word)
    if stage == "ask_name":
        _on_name(case, body)
    elif stage == "ask_where":
        _on_where(case, body, tri)
    elif stage == "ask_income":
        _on_income(case, word)
    elif stage == "ask_slot":
        _on_slot(case, word)
    elif stage == "ask_identity":
        _on_identity(case, body)
    elif stage == "ask_callhelp":
        _on_callhelp(case, word, body)
    else:
        _notify_chws(case, f"[#{case_id}] {_tail(phone)}: {body}")


def _is_free_text(word: str) -> bool:
    """Worth sending to the LLM: real words, not a menu number or a one-word command."""
    return len(word) >= 4 and not word.isdigit() and word not in STOP_WORDS | START_WORDS | CANCEL_VISIT | RESCHEDULE | {"help", "list"}


def _find_place(body: str, tri: dict | None) -> dict | None:
    """A ZIP or town typed in the text, else the place the LLM picked out of it."""
    return (services.find_place(body)
            or (services.find_place(tri["place"]) if tri and tri.get("place") else None)
            or understand.place(body))  # Claude, when a key is set (e.g. a misspelled town)


def _new_case(phone: str, body: str, tri: dict) -> None:
    first_contact = db.is_first_contact(phone)
    case_id = db.create_case(phone, tri)
    db.add_message(case_id, "patient", phone, config.CLINIC_NUMBER, body, "inbound")
    case = db.get_case(case_id)
    if first_contact:  # carrier opt-in confirmation: program name, rates, HELP and STOP
        sms.send(phone, _t(tri["language"], "welcome"), case_id, "system")
    _notify_chws(case, f"[#{case_id} {tri['urgency'].upper()} {tri['category']}] {_tail(phone)}: {body}\n"
                       f"Summary: {tri['summary']}\nReply: #{case_id} your message")
    place = _find_place(body, tri)
    if place:  # remember a place named in the first text for after sign-up
        db.update_case(case_id, zip=place["zip"], town=place["town"])
    patient = db.get_patient(phone)
    if patient is None or patient["first_name"] is None:
        # New number (or never answered): create the account, ask for a name first.
        # first_name: NULL = not asked/answered yet, "" = texted SKIP, else their name.
        db.create_patient(phone, tri["language"])
        db.update_case(case_id, stage="ask_name")
        sms.send(phone, _t(tri["language"], "ask_name"), case_id, "system")
        return
    db.update_patient(phone)
    _continue_after_hello(db.get_case(case_id), patient)


def _continue_after_hello(case, patient) -> None:
    """Returning person (or just signed up): go to the place, or ask for it."""
    first = (patient["first_name"] or "").strip() if patient else ""
    if case["zip"]:
        _ask_income(case, services.find_place(case["zip"]))
    elif first:
        db.update_case(case["id"], stage="ask_where")
        sms.send(case["patient_phone"], _t(case["language"], "welcome_back", first=first[:15]), case["id"], "system")
    else:
        db.update_case(case["id"], stage="ask_where")
        sms.send(case["patient_phone"], _t(case["language"], "start"), case["id"], "system")


NAME_WORD = re.compile(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’.-]*$")


def _on_name(case, body: str) -> None:
    phone, lang = case["patient_phone"], case["language"]
    words = body.strip().split()
    patient = db.get_patient(phone) or db.create_patient(phone, lang)
    code = db.patient_code(patient["id"])
    if len(words) == 1 and words[0].lower() == "skip":
        db.update_patient(phone, first_name="", last_name="")
        sms.send(phone, _t(lang, "account_noname", code=code), case["id"], "system")
    elif 1 <= len(words) <= 4 and all(NAME_WORD.match(w) for w in words) and not GREETING.match(body.strip()):
        first, last = words[0].title(), (words[-1].title() if len(words) > 1 else "")
        db.update_patient(phone, first_name=first, last_name=last)
        sms.send(phone, _t(lang, "account", first=first[:15], code=code), case["id"], "system")
        _notify_chws(case, f"[#{case['id']}] {first} {last} signed up as {code}")
    else:
        sms.send(phone, _t(lang, "name_again"), case["id"], "system")
        return
    _continue_after_hello(db.get_case(case["id"]), None)


def _send_my_id(phone: str, case) -> None:
    lang = case["language"] if case else "en"
    patient = db.get_patient(phone)
    db.add_message(case["id"] if case else None, "patient", phone, config.CLINIC_NUMBER, "MY ID", "inbound")
    if not patient:
        sms.send(phone, _t(lang, "start"), case["id"] if case else None, "system")
        return
    name = " ".join(x for x in (patient["first_name"], patient["last_name"]) if x)
    sms.send(phone, _t(lang, "my_id", code=db.patient_code(patient["id"]), name=f" ({name})" if name else ""),
             case["id"] if case else None, "system")


def _ask_income(case, place: dict) -> None:
    db.update_case(case["id"], stage="ask_income", zip=place["zip"], town=place["town"])
    db.update_patient(case["patient_phone"], zip=place["zip"], town=place["town"])
    sms.send(case["patient_phone"], _t(case["language"], "place_ok", **place), case["id"], "system")


GREETING = re.compile(r"^(hi+|hey+|hello+|hiya|yo|hola|good (morning|afternoon|evening)|ok(ay)?|thanks?|thank you)( there)?\W*$", re.I)


def _on_where(case, body: str, tri: dict | None = None) -> None:
    place = _find_place(body, tri)
    if not place:
        # A greeting isn't a failed place: ask the friendly first question again.
        key = "start" if GREETING.match(body.strip()) else "where_again"
        sms.send(case["patient_phone"], _t(case["language"], key), case["id"], "system")
        return
    if case["category"] == "other":  # first text was just "hi": sort the need from this one
        tri = tri or triage(body)
        db.update_case(case["id"], category=tri["category"], summary=tri["summary"],
                       urgency=max(case["urgency"], tri["urgency"], key=["low", "medium", "high", "emergency"].index))
    _ask_income(case, place)


def _place_of(case) -> dict | None:
    return services.find_place(case["zip"] or "")


def _on_income(case, word: str) -> None:
    if word in YES:
        income = "low"
    elif word in NO:
        income = "standard"
    else:
        sms.send(case["patient_phone"], _t(case["language"], "income_again"), case["id"], "system")
        return
    db.update_case(case["id"], income=income)
    db.update_patient(case["patient_phone"], income=income)
    _offer(db.get_case(case["id"]), services.options(income, _place_of(case)), "options")


def _offer(case, opts: list[dict], header_key: str, **kw) -> None:
    lang, phone = case["language"], case["patient_phone"]
    if not opts:
        db.update_case(case["id"], stage="done", offers=None)
        sms.send(phone, _t(lang, "none_found"), case["id"], "system")
        _notify_chws(case, f"[#{case['id']}] no open times near {case['town']}; please set one up.")
        return
    db.update_case(case["id"], stage="ask_slot", offers=json.dumps(opts))
    header = _t(lang, header_key, n=len(opts), **kw)
    sms.send(phone, services.option_lines(opts, header), case["id"], "system")


def _visit_fields(service_id: str, ts: float) -> dict:
    svc = services.clinics()[service_id]
    return {"short": svc["short"], "when": services.when(ts), "address": svc["address"],
            "town": svc["town"], "phone": svc["phone"]}


def _on_slot(case, word: str) -> None:
    opts = json.loads(case["offers"] or "[]")
    lang, phone = case["language"], case["patient_phone"]
    if word == "0":
        if case["resched_id"]:
            old = db.get_booking(case["resched_id"])
            db.update_case(case["id"], stage="booked", offers=None, resched_id=None)
            sms.send(phone, _t(lang, "kept", **_visit_fields(old["service_id"], old["start_ts"])), case["id"], "system")
        else:
            db.update_case(case["id"], stage="done", offers=None)
            sms.send(phone, _t(lang, "declined"), case["id"], "system")
            _notify_chws(case, f"[#{case['id']}] none of the offered times worked; please text them.")
        return
    if not (word.isdigit() and 1 <= int(word) <= len(opts)):
        sms.send(phone, _t(lang, "slot_again", n=len(opts)), case["id"], "system")
        return

    pick = opts[int(word) - 1]
    if services.clinics().get(pick["service_id"], {}).get("kind") == "call":
        _offer_call(case, pick)
        return
    if services.uses_athena(pick["service_id"]) and not db.athena_patient(phone):
        db.update_case(case["id"], stage="ask_identity", pick=json.dumps(pick))
        patient = db.get_patient(phone)
        key = "identity_dob" if patient and patient["first_name"] and patient["last_name"] else "identity"
        sms.send(phone, _t(lang, key, short=pick["short"]), case["id"], "system")
        return
    _book_pick(case, pick)


def _offer_call(case, pick: dict) -> None:
    """Real mobile clinic stops book by phone: give the number, offer a health worker's help."""
    svc = services.clinics()[pick["service_id"]]
    when = f"{services.when(pick['ts']).rsplit(' ', 1)[0]} {services.hours(svc)}"
    db.update_case(case["id"], stage="ask_callhelp", pick=json.dumps(pick), offers=None)
    sms.send(case["patient_phone"], _t(case["language"], "call_info", when=when, where=svc["address"],
                                       town=svc["town"], phone=svc["phone"]), case["id"], "system")


def _on_callhelp(case, word: str, body: str) -> None:
    pick = json.loads(case["pick"] or "{}")
    svc = services.clinics().get(pick.get("service_id"), {})
    db.update_case(case["id"], stage="done", pick=None)
    if word in YES:
        patient = db.get_patient(case["patient_phone"])
        who = " ".join(x for x in (patient["first_name"], patient["last_name"]) if x) if patient else ""
        sms.send(case["patient_phone"], _t(case["language"], "callhelp_ok"), case["id"], "system")
        _notify_chws(case, f"[#{case['id']}] please call {svc.get('phone', '')} to book {who or _tail(case['patient_phone'])} "
                           f"at the mobile clinic, {svc.get('town', '')} {svc.get('date', '')} {svc.get('start', '')} "
                           f"({svc.get('address', '')}), then text them: #{case['id']} your message")
    else:
        _notify_chws(case, f"[#{case['id']}] {_tail(case['patient_phone'])}: {body}")


def _send_mobile_route(phone: str, case, body: str) -> None:
    lang = case["language"] if case else "en"
    db.add_message(case["id"] if case else None, "patient", phone, config.CLINIC_NUMBER, body, "inbound")
    route = services.mobile_route()
    stops = services.upcoming_mobile(3)
    if not stops:
        sms.send(phone, _t(lang, "mobile_none", phone=route["phone"]), case["id"] if case else None, "system")
        return
    lines = [_t(lang, "mobile_list", phone=route["phone"])]
    for st in stops:
        day = services.when(services.free_slots(st, 1)[0]["ts"]).rsplit(" ", 1)[0]
        lines.append(f"{day} {st['town']} {services.hours(st)}")
    sms.send(phone, "\n".join(lines), case["id"] if case else None, "system")


def _book_pick(case, pick: dict) -> None:
    lang, phone, sid = case["language"], case["patient_phone"], pick["service_id"]
    ext = None
    if services.uses_athena(sid):
        try:
            ok = athena.client.book(pick["ext"], db.athena_patient(phone),
                                    services.clinics()[sid]["athena"], pick.get("atype"))
        except Exception as e:
            _booking_down(case, e, f"book {pick['short']} {services.when(pick['ts'])}")
            return
        services.forget_athena_slots(sid)
        if not ok:  # taken in athenahealth in the meantime
            _offer(case, services.next_times(sid, _place_of(case)), "taken", short=pick["short"])
            return
        ext = pick["ext"]
    booking_id = db.book(case["id"], phone, sid, pick["ts"], ext)
    if booking_id is None:  # someone else got it first
        _offer(case, services.next_times(sid, _place_of(case)), "taken", short=pick["short"])
        return

    fields = _visit_fields(sid, pick["ts"])
    if case["resched_id"]:
        old = db.get_booking(case["resched_id"])
        try:
            _cancel_external(old)
        except Exception as e:  # new visit is booked; a person must cancel the old one
            log.error("could not cancel old athenahealth visit %s: %s", old["external_id"], e)
            _notify_chws(case, f"[#{case['id']}] please cancel the old visit in athenahealth: "
                               f"appointment {old['external_id']}")
        db.cancel_booking(old["id"])
        sms.send(phone, _t(lang, "moved", **fields), case["id"], "system")
        note = "rescheduled to"
    else:
        sms.send(phone, _t(lang, "booked", **fields), case["id"], "system")
        note = "booked"
    db.update_case(case["id"], stage="booked", offers=None, resched_id=None, pick=None)
    _notify_chws(case, f"[#{case['id']}] {note} {fields['short']} {fields['when']} "
                       f"({case['income']} income, {case['zip']})" + (f" athena appt {ext}" if ext else ""))


DOB = re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")


def _parse_identity(text: str) -> tuple[str, str, str] | None:
    """'Jane Q Doe 3/14/1980' -> ('Jane', 'Doe', '03/14/1980'); None if it doesn't look right."""
    m = DOB.search(text)
    if not m:
        return None
    names = re.findall(r"[A-Za-zÀ-ÿ'’-]+", text[:m.start()])
    if len(names) < 2:
        return None
    mo, d, y = map(int, m.groups())
    today = dt.date.today()
    if y < 100:
        y += 1900 if y > today.year % 100 else 2000
    try:
        born = dt.date(y, mo, d)
    except ValueError:
        return None
    if not (today.year - 120 < y and born < today):
        return None
    return names[0].title(), names[-1].title(), born.strftime("%m/%d/%Y")


def _on_identity(case, body: str) -> None:
    lang, phone = case["language"], case["patient_phone"]
    parsed = _parse_identity(body)
    patient = db.get_patient(phone)
    if not parsed and patient and patient["first_name"] and patient["last_name"]:
        dob = _parse_identity(f"{patient['first_name']} {patient['last_name']} {body}")
        parsed = dob
    if not parsed:
        sms.send(phone, _t(lang, "identity_again"), case["id"], "system")
        return
    pick = json.loads(case["pick"])
    first, last, dob = parsed
    try:
        pid = athena.client.find_or_create_patient(
            first, last, dob, phone, case["zip"] or "", services.clinics()[pick["service_id"]]["athena"]["departmentid"])
    except Exception as e:
        _booking_down(case, e, "find or create the patient record")
        return
    db.save_athena_patient(phone, pid)
    db.update_case(case["id"], stage="ask_slot", pick=None)
    _book_pick(db.get_case(case["id"]), pick)


def _cancel_external(booking) -> None:
    if booking and booking["external_id"] and services.uses_athena(booking["service_id"]):
        athena.client.cancel(booking["external_id"], db.athena_patient(booking["patient_phone"]))
        services.forget_athena_slots(booking["service_id"])


def _booking_down(case, err: Exception, what: str) -> None:
    log.error("athenahealth failed (%s) for case %s: %s", what, case["id"], err)
    services.forget_athena_slots()  # don't keep offering times we just failed to reach
    db.update_case(case["id"], stage="done", offers=None, pick=None)
    sms.send(case["patient_phone"], _t(case["language"], "system_down"), case["id"], "system")
    _notify_chws(case, f"[#{case['id']}] athenahealth is not answering; please {what} by phone.")


def _change_visit(phone: str, body: str, word: str, case) -> None:
    booking = db.active_booking(phone)
    if booking:
        case = db.get_case(booking["case_id"])
        if case["status"] != "open":
            db.update_case(case["id"], status="open")
            case = db.get_case(case["id"])
    case_id = case["id"] if case else None
    db.add_message(case_id, "patient", phone, config.CLINIC_NUMBER, body, "inbound")
    lang = case["language"] if case else "en"
    if not booking:
        sms.send(phone, _t(lang, "no_booking"), case_id, "system")
        return
    fields = _visit_fields(booking["service_id"], booking["start_ts"])
    if word in CANCEL_VISIT:
        try:
            _cancel_external(booking)
        except Exception as e:
            _booking_down(case, e, f"cancel {fields['short']} {fields['when']}")
            return
        db.cancel_booking(booking["id"])
        db.update_case(case_id, stage="done", offers=None, resched_id=None)
        sms.send(phone, _t(lang, "cancelled", **fields), case_id, "system")
        _notify_chws(case, f"[#{case_id}] cancelled {fields['short']} {fields['when']}")
        return
    # Reschedule: keep the old visit until a new one is picked.
    opts = [o for o in services.options(case["income"] or "low", _place_of(case) or services.places()[0])
            if not (o["service_id"] == booking["service_id"] and o["ts"] == booking["start_ts"])]
    db.update_case(case_id, resched_id=booking["id"])
    sms.send(phone, _t(lang, "resched", **fields), case_id, "system")
    _offer(db.get_case(case_id), opts, "options")


def send_due_reminders(at: float | None = None) -> int:
    """Text everyone whose visit is within 24 hours and who has not been reminded."""
    at = db.now() if at is None else at
    sent = 0
    for b in db.due_reminders(at):
        case = db.get_case(b["case_id"])
        sms.send(b["patient_phone"], _t(case["language"] if case else "en", "reminder",
                                        **_visit_fields(b["service_id"], b["start_ts"])), b["case_id"], "system")
        db.mark_reminded(b["id"])
        sent += 1
    return sent


# ---------------------------------------------------------------- CHWs
def _from_chw(chw: str, body: str) -> None:
    if body.lower() == "list":
        db.add_message(None, "chw", chw, config.CLINIC_NUMBER, body, "inbound")
        open_cases = db.list_cases("open")[:8]
        lines = [f"#{c['id']} {c['urgency']} {c['category']} {_tail(c['patient_phone'])}"
                 + (" booked" if c["booked"] else "") + (" (you)" if c["assigned_chw"] == chw else "")
                 for c in open_cases]
        sms.send(chw, "Open cases:\n" + "\n".join(lines) if lines else "No open cases.", None, "system")
        return

    m = CHW_REPLY.match(body)
    if not m:
        db.add_message(None, "chw", chw, config.CLINIC_NUMBER, body, "inbound")
        sms.send(chw, "Start with the case number, e.g. \"#12 I can drive you Tuesday at 9\". "
                      "\"#12 close\" closes it, LIST shows open cases.", None, "system")
        return

    case_id, text = int(m.group(1)), m.group(2).strip()
    case = db.get_case(case_id)
    if case is None:
        sms.send(chw, f"No case #{case_id}.", None, "system")
        return
    db.add_message(case_id, "chw", chw, config.CLINIC_NUMBER, body, "inbound")

    if text.lower() == "close":
        db.update_case(case_id, status="closed")
        sms.send(chw, f"#{case_id} closed.", case_id, "system")
        return
    if not text:
        sms.send(chw, f"Add a message after #{case_id}.", case_id, "system")
        return
    if case["status"] != "open":
        sms.send(chw, f"#{case_id} is closed; the patient's next text opens a new case.", case_id, "system")
        return

    if not case["assigned_chw"]:
        db.update_case(case_id, assigned_chw=chw)
        for other in config.CHW_NUMBERS:
            if other != chw:
                sms.send(other, f"#{case_id} taken by {_tail(chw)}.", case_id, "system")
    sms.send(case["patient_phone"], text, case_id, "chw")


def reply_from_dashboard(case_id: int, text: str) -> bool:
    case = db.get_case(case_id)
    if case is None or case["status"] != "open" or not text.strip():
        return False
    sms.send(case["patient_phone"], text.strip(), case_id, "dashboard")
    return True
