"""Real texts through the Messages app on this Mac.

The Mac sends and receives with the linked iPhone's own number (iPhone: Settings > Messages >
Text Message Forwarding > this Mac), so no carrier registration is needed.
Sending uses AppleScript; incoming texts are read from the Messages database, which needs
Full Disk Access for the app that runs the server (System Settings > Privacy & Security).
"""
import logging
import sqlite3
import subprocess

from . import config

log = logging.getLogger("mac_messages")

# Number and text are passed as arguments, never pasted into the script, so quotes in a
# patient's text cannot break it.
_SEND = """on run argv
  tell application "Messages"
    set smsAccount to 1st account whose service type = SMS
    send (item 2 of argv) to buddy (item 1 of argv) of smsAccount
  end tell
end run"""


def send(to: str, body: str) -> str:
    r = subprocess.run(["osascript", "-e", _SEND, to, body], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"Messages: {(r.stderr or r.stdout).strip()[:200]}")
    return ""


def _db() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{config.MAC_MESSAGES_DB}?mode=ro", uri=True, timeout=5)


def latest_rowid() -> int:
    with _db() as con:
        return con.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0]


def _text_from_blob(blob: bytes | None) -> str:
    """Newer macOS keeps the text only in attributedBody (an archived NSAttributedString)."""
    if not blob:
        return ""
    i = blob.find(b"NSString")
    if i < 0:
        return ""
    i += len(b"NSString") + 5
    if i >= len(blob):
        return ""
    n = blob[i]
    i += 1
    if n == 0x81:
        n = int.from_bytes(blob[i:i + 2], "little")
        i += 2
    elif n == 0x82:
        n = int.from_bytes(blob[i:i + 3], "little")
        i += 3
    return blob[i:i + n].decode("utf-8", "replace")


def new_messages(after: int) -> list[tuple[int, str, str]]:
    """Incoming texts (SMS or iMessage) newer than ROWID `after`: (rowid, sender, text).
    Own sends, reactions and attachments without text are left out."""
    with _db() as con:
        rows = con.execute(
            "SELECT m.ROWID, h.id, m.text, m.attributedBody FROM message m "
            "JOIN handle h ON h.ROWID = m.handle_id "
            "WHERE m.ROWID > ? AND m.is_from_me = 0 AND m.item_type = 0 "
            "AND m.associated_message_type = 0 ORDER BY m.ROWID", (after,)).fetchall()
    out = []
    for rowid, sender, text, blob in rows:
        text = (text or _text_from_blob(blob)).replace("￼", "").strip()
        out.append((rowid, sender or "", text))
    return out


def allowed(phone: str) -> bool:
    """MAC_ALLOWED_SENDERS keeps personal texts to this number out of CareLine.
    Blank = everyone who texts this number gets CareLine replies."""
    if not phone.startswith("+"):
        return False
    if not config.MAC_ALLOWED_SENDERS:
        return True
    from .sms import normalize

    return phone in {normalize(p) for p in config.MAC_ALLOWED_SENDERS + config.CHW_NUMBERS}
