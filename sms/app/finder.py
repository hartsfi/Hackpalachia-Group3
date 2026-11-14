"""The clinic finder: which clinics to offer a patient, starting from their ZIP code.

Plain rules, no model calls. It never judges how sick someone is: emergencies are answered
with 911 / 988 in relay.py before any booking question is asked.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, wait

from . import clinics, config, services

log = logging.getLogger("finder")


def rank_miles(svc: dict, place: dict, need: str | None) -> float:
    """Distance used for ranking: real miles, minus NEED_TAG_BONUS_MILES when the clinic is
    tagged for the patient's need."""
    bonus = config.NEED_TAG_BONUS_MILES if need and need in svc.get("tags", ()) else 0
    return services.miles(place, svc) - bonus


def candidates(place: dict, income: str, need: str | None = None) -> list[dict]:
    """Stage 1, from the directory alone (no calls): clinics that are not hidden, take this
    income group and lie within MAX_CLINIC_MILES; the best-ranked CANDIDATES_TO_CHECK of them."""
    fit = [s for s in clinics.offered()
           if not s.get("hidden") and s.get("for", "all") in (income, "all")
           and services.miles(place, s) <= config.MAX_CLINIC_MILES]
    fit.sort(key=lambda s: rank_miles(s, place, need))
    return fit[:max(1, config.CANDIDATES_TO_CHECK)]


def recommend(place: dict | None, income: str, need: str | None = None, limit: int = 3) -> list[dict]:
    """Up to `limit` clinics for this patient, best first, each with its next open time.

    Returns options in the shape services._option() makes:
      {"service_id", "short", "ts", "ext", "atype", "mi"}

    Stage 1 (directory only): see candidates().
    Stage 2: the next open time of each candidate, fetched side by side with
      ATHENA_SLOT_TIMEOUT seconds each, asking athenahealth for the appointment type mapped to
      the patient's need (else the clinic's default type). A clinic whose call fails or is too
      slow is left out; if every call fails the answer is [] and relay.py tells the patient a
      health worker will set a visit up.

    Ranking, one rule: nearest first, where a clinic tagged for the need counts as
    NEED_TAG_BONUS_MILES (15) miles closer than it is. So a tagged clinic beats an untagged
    one unless the untagged one is more than 15 miles nearer. Clinics the same whole number of
    miles away (after that head start) go by the sooner open time. One option per clinic.
    """
    if not place:
        return []
    cands = candidates(place, income, need)
    if not cands:
        return []
    timeout = config.ATHENA_SLOT_TIMEOUT
    started = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=len(cands), thread_name_prefix="slots")
    jobs = {pool.submit(services.free_slots, s, 1, None, need, timeout, True): s for s in cands}
    done, late = wait(jobs, timeout=timeout + 1)
    pool.shutdown(wait=False, cancel_futures=True)  # a slow call ends by its own timeout

    found, failed = [], [jobs[j]["id"] for j in late]
    for job in done:
        svc = jobs[job]
        try:
            slots = job.result()
        except Exception:  # already logged with the athenahealth error by services
            failed.append(svc["id"])
            continue
        if slots:
            found.append((round(rank_miles(svc, place, need)), slots[0]["ts"], services.miles(place, svc), svc, slots[0]))
    found.sort(key=lambda f: f[:3])
    if failed:
        log.warning("clinic finder: %d of %d clinics skipped, no answer in time or an error: %s",
                    len(failed), len(cands), ", ".join(sorted(failed)))
    log.info("clinic finder: %d candidates, %d with open times, need=%s, %.1fs",
             len(cands), len(found), need, time.monotonic() - started)
    return [services._option(svc, slot, place) for _, _, _, svc, slot in found[:limit]]
