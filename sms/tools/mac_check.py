"""Check SMS_PROVIDER=mac before starting the server.

  uv run python -m tools.mac_check               # can this Mac read incoming texts?
  uv run python -m tools.mac_check +17405551234  # also text that number from the iPhone's number
"""
import sqlite3
import sys

from app import config, mac_messages
from app.sms import normalize

try:
    last = mac_messages.latest_rowid()
    recent = mac_messages.new_messages(max(0, last - 50))[-3:]
    print(f"OK: reading {config.MAC_MESSAGES_DB} (newest message {last})")
    for _, sender, text in recent:
        print(f"  from {sender}: {text[:60]!r}")
except sqlite3.OperationalError as e:
    print(f"CANNOT READ {config.MAC_MESSAGES_DB}: {e}")
    print("Give the app you run this from Full Disk Access: System Settings > Privacy & Security >"
          " Full Disk Access, switch it on (add it with + if missing), then quit and reopen that app.")

if len(sys.argv) > 1:
    to = normalize(sys.argv[1])
    mac_messages.send(to, "CareLine test: texts from this Mac work.")
    print(f"sent a test text to {to}")
