# Hackpalachia-Group3

## Parts

- [`sms/`](sms/) : **TextBridge**, a text line that connects rural patients with community health
  workers from any phone, no internet needed. See [`sms/README.md`](sms/README.md) and
  [`sms/TESTING.md`](sms/TESTING.md).
- [`docs/USER_STORIES.md`](docs/USER_STORIES.md) : user stories (booking by text, medication prices
  and nearest hospitals) and which acceptance criteria are done.
- [`agent/`](agent/) : a conversational booking agent and its data layer. It understands free-text
  messages and books through tool calls. Self-contained with its own demo data and test console;
  not yet wired into `sms/`. See [`agent/README.md`](agent/README.md).
