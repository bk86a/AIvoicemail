# aivoicemail - design (v0.1)

Self-hosted, GDPR-first AI voicemail: Asterisk answers, local Whisper transcribes, an OpenAI-compatible LLM (EU or local) summarises, the business gets an email.

Status: approved design, 2026-09-22. Derived from a production deployment for two EU companies; this repository is a clean re-implementation with no deployment-specific data or history.

## 1. Goals and non-goals

**Target user (v0.1):** a technically capable small business running one VPS for its own number(s). The config supports several lines and mailboxes, so an IT integrator can run one install per client.

**Goals**

- Answer inbound calls on one or more numbers, play a language menu (optional) and a recording/AI transparency notice (EU AI Act Art. 50), record a message, email a transcript and structured summary to the line's mailbox.
- Keep personal data minimal and local by default: local speech recognition, audio deleted after delivery, metadata call log with fixed retention.
- Inbound only, hardened by default: no module able to originate calls, no management interfaces, SIP accepted only from the carrier's address ranges.
- One config file, `docker compose up`, a `check` command that catches mistakes before the first call.

**Non-goals for v0.1** (roadmap candidates): web UI, database, multi-tenant installs, SIP registration (username/password) trunks (planned v0.2), webhook delivery (planned v0.2), outbound calling (never), live transcription, cloud-specific firewall tooling.

## 2. Architecture

```
┌──────────────── one host (docker compose) ─────────────────┐
│  asterisk (read-only, UDP 5060 + RTP, IP-identified trunk) │
│      │ records to                                           │
│      ▼                                                      │
│  spool volume  ready/<uuid>.{wav,json}                      │
│      │ read by                                              │
│      ▼                                                      │
│  worker: speech check → transcribe → summarise → SMTP       │
│          → ack (delete) → call log                          │
└────────────────────────────────────────────────────────────┘
split mode: asterisk on host A, worker on host B, spool read over SSH (vm-spool)
```

Two images are built from this repository: `aivoicemail-asterisk` and `aivoicemail-worker`.

### Repository layout

```
aivoicemail/
  config/aivoicemail.example.toml   the one config file (example)
  aivoicemail/                      Python package
    config.py      load and validate the config
    generate.py    config → Asterisk files (pjsip, extensions, dids) + prompt list
    spool/         base.py (interface), local.py, ssh.py; vm-spool forced command
    stt/           whisper_local.py, openai_compatible.py
    llm/           openai_compatible.py, anthropic.py (optional)
    tts/           piper.py, azure.py
    mail.py        SMTP delivery
    processor.py   per-item state machine
    calllog.py, alerts.py, cli.py, __main__.py
  asterisk/        Dockerfile, hardened base config, vm-finalize hangup script
  prompts/         notice/menu/thank-you templates per language (nl, fr, de, en, pl to start)
  deploy/          compose.yaml (single host), compose.telephony.yaml + compose.worker.yaml (split),
                   nftables example, logrotate files
  tests/           pytest suites, SIPp scenarios and harness
  docs/            install, config reference, carriers, GDPR/AI Act notes, security
```

### Interfaces

- **Spool** (`spool.base.Spool`): `list() -> [Item(id, mtime, has_audio)]`, `get(id, dest) -> (meta, wav|None)`, `ack(id)`. Backends: `local` (default; shared volume) and `ssh` (split mode; talks to the `vm-spool` forced command: `list`, `get <uuid>`, `ack <uuid>`, UUIDs matched with `fullmatch`, tar members restricted to `<id>.json` / `<id>.wav` regular files).
- **STT** (`stt.*`): `transcribe(wav, lang) -> text`; `speech_seconds(wav) -> float` on the local engine. Chain order and retries from config; empty text is a failure; a missing key skips the provider.
- **LLM** (`llm.*`): `summarise(transcript, meta) -> dict` matching the summary schema (caller_name, company, subject, callback_number, language, urgency, summary, requested_action). One `openai_compatible` adapter covers Mistral, OpenAI, Azure OpenAI, Ollama, vLLM and other compatible endpoints; `structured = "json_schema" | "json_object" | "none"` selects enforcement. Output always passes normalise → validate; a language chosen in the menu overrides the model's value.
- **TTS** (`tts.*`): `render(segments) -> wav` (8 kHz mono 16-bit). Engines: Piper (default, local) and Azure (optional; phoneme overrides, exact-silence control, per-segment and per-sentence pauses).
- **Mail** (`mail.send(to, subject, text, attachments) -> message_id`): SMTP with STARTTLS/TLS; success means the server accepted the message.

### CLI

`aivoicemail check` · `aivoicemail generate` · `aivoicemail render-prompts` · `aivoicemail test-call` (local SIPp smoke test, optionally with fake providers) · `aivoicemail worker` (the service entry point).

## 3. Configuration

One TOML file; secrets only from environment variables or a `.env` file (named by `*_env` keys).

```toml
[company]
name = "ACME BV"
privacy_url_default = "acme.example/privacy"

[trunk]                       # IP-authenticated only in v0.1
provider = "didww"            # label only; the ranges are what counts
signalling_ranges = ["46.19.208.0/21", "185.238.172.0/22"]
media_ranges      = ["46.19.208.0/21", "185.238.172.0/22"]
public_ip = "203.0.113.10"    # NAT: external signalling/media address
local_net = "10.0.0.0/24"     # optional

[[line]]
id = "be"
did = "3220000001"            # E.164 without +
mailbox = "info@acme.example"
email_language = "en"
menu = ["nl", "fr", "en"]     # one language = no menu
default_language = "auto"     # no key pressed: multilingual short notice, language auto-detected
privacy_url = "acme.example/privacy"   # optional, per line; else company default

[[line]]
id = "pl"
did = "48320000001"
mailbox = "kontakt@acme.example"
email_language = "pl"
menu = ["pl"]

[recording]
max_seconds = 180
silence_seconds = 5
min_speech_seconds = 2

[stt]
chain = ["whisper_local", "remote"]  # "remote" optional
whisper_model = "large-v3"           # small / medium for small hosts
remote = { base_url = "https://api.mistral.ai/v1", model = "voxtral-mini-latest", key_env = "STT_API_KEY" }

[llm]
chain = ["primary"]                  # add "secondary" for a fallback
primary = { base_url = "https://api.mistral.ai/v1", model = "mistral-small-latest", key_env = "LLM_API_KEY", structured = "json_schema" }

[mail]
smtp_host = "smtp.example"
smtp_port = 587
smtp_user_env = "SMTP_USER"
smtp_password_env = "SMTP_PASSWORD"
from = "voicemail@acme.example"
from_name = "ACME Voicemail"
alert_to = "admin@acme.example"

[tts]
engine = "piper"                     # or "azure"
voices = { nl = "nl_BE-nathalie-medium", fr = "fr_FR-siwis-medium", en = "en_GB-alba-medium", pl = "pl_PL-gosia-medium" }
pronunciation = { "ACME" = { nl = "ˈaː.kmə", en = "ˈæk.mi" } }

[retention]
call_log_days = 90
cdr = false                          # optional Asterisk CDR CSV, same retention when on
```

Prompts come from `prompts/<lang>.toml` templates with `{company}` and `{privacy_url}` placeholders; installs may override any text, and a file `overrides/<prompt>.wav` replaces any rendered prompt (e.g. a recorded human voice).

`aivoicemail check` validates: DIDs digits-only and unique; CIDR syntax; every menu language has templates and a configured voice; every notice (rendered text) still contains the transparency elements (recorded, automated system, artificial intelligence, privacy URL) - enforced per language by a keyword list in the template; named environment variables exist; `.env` not world-readable; configured endpoints reachable (optional `--online`).

## 4. Call and data flow

1. **Call in.** PJSIP accepts SIP only from `trunk.signalling_ranges` (identify by IP; no auth objects; no anonymous endpoint). Unidentified sources get 401 (digest authenticator loaded, no credentials exist). The called number is filtered to digits; any other character, an empty number or an unknown DID ends the call with 404. DTMF `auto`: RFC 4733 when the offer carries telephone-event, in-band detection otherwise. Docs tell users to enable telephone-event at the carrier.
2. **Greeting.** Menu (if more than one language) → notice → beep → record (max seconds, silence stop, hang-up keeps the recording) → thank-you when recording stops on silence or time limit.
3. **Finalise.** Hangup handler writes `<uuid>.wav`, then `<uuid>.json` (moved into `ready/` last): line, DID, caller (`+` prefixed E.164, or `withheld`), language choice, start/end, duration, has_audio. Every argument reaching the script is digits-only or a constant, single-quoted; the script re-sanitises every field.
4. **Worker loop** (every 30 s): list; per item: speech check (below `min_speech_seconds` → missed-call email) → STT chain → LLM chain → render email in the line's `email_language` (transcript in the caller's language) → SMTP → ack → call log line. Either chain exhausted → fallback email with the WAV attached.
5. **Failure semantics.** Send failure: no ack, retry next cycle reusing the rendered email (no second provider run). Ack failure: retry the ack only (no duplicate email). Five consecutive failures of an item: fallback email (WAV attached if present) + alert, then ack. Alerts to `alert_to`, never containing personal data: item older than 60 min; spool unreachable for 15 min (single host: directory missing/unreadable; split: SSH failure); at most one per condition per hour.
6. **Data handling.** Audio lives in the spool until delivery; the worker processes it in a tmpfs work directory. Call log: JSON lines (id, line, DID, caller, language, start, duration, outcome, providers, message id; no content), rotated daily, deleted after `call_log_days`. Asterisk console at notice level (no caller numbers in container logs); container log size capped. Journal/stdout carry IDs and outcomes only. Optional CDR CSV off by default.

## 5. Security baseline and deployment

**Defaults (weakening requires an explicit config change):**

- Containers read-only, `cap_drop: ALL`, `no-new-privileges`, non-root uid.
- Asterisk module allowlist; a test fails the build if any origination module (Dial, Originate, CLI originate, spool files, FollowMe, Queue, Page, IAX2) or AMI/ARI/HTTP is loadable.
- Asterisk runs with host networking (SIP/RTP behind NAT). A host firewall is required: an nftables example ships (separate input table, policy drop, SIP/RTP only from trunk ranges, existing chains untouched, timed rollback recipe), plus cloud firewall guidance (including merging port ranges when rule quotas are small).
- Split mode: restricted forced-command key (`restrict,from=<worker IP>,command=vm-spool`), host-key pinning, strict tar member checks.
- Secrets from env/`.env` only; `check` warns on a world-readable `.env`.
- Supply chain: pinned base image and Asterisk package version, pinned Python dependencies with hashes, Dependabot, CI running pytest and the SIPp harness on every pull request.

**Deployment**

- Single host: clone → copy the example config → fill `.env` → `aivoicemail check` → `aivoicemail render-prompts` → `docker compose up -d` → `aivoicemail test-call`.
- Split: `compose.telephony.yaml` on the SIP host, `compose.worker.yaml` on the processing host, documented key exchange.
- Updates: `git pull && docker compose up -d --build`; the spool persists, in-flight items are picked up again.
- Sizing: Whisper large-v3 on CPU ≈ 4 GB RAM and 4 cores for near-real-time; `small`/`medium` for small hosts; remote-only STT needs no model.

## 6. Testing

- **Unit:** config validation; generator golden files (sample configs → expected pjsip/extensions/dids); spool backends; STT/LLM adapters (request shapes against stubbed HTTP); normalise/validate; processor state machine (send/ack failure, poison items, cached resend); alerts; call log; TTS SSML/phoneme/pause building; prompt template transparency check.
- **Security:** forbidden-module check; injection INVITEs via called number and caller ID (quotes, `$()`, backticks, CRLF, `${...}`) → 404 or sanitised caller, no marker file; unidentified source → 401; unknown DID → 404; container log contains no caller numbers.
- **SIPp harness (CI):** menu keys via RFC 4733 and in-band DTMF, timeout path, single-language line, hang-up during greeting, unknown DID; pauses derived from rendered prompt durations.
- **Fake providers mode** for CI and first-run tests: fixed transcript and summary, SMTP to a local capture; usable by `aivoicemail test-call`.

## 7. Documentation, licence, publication

- Docs: README (what, privacy stance, quickstart), install (single/split), config reference, carriers (DIDWW first, with the telephone-event note; others "community-tested" once confirmed), GDPR/AI Act notes (privacy-notice elements, retention defaults; a checklist, not legal advice), `SECURITY.md`, CHANGELOG.
- Licence: AGPL-3.0 (owner may switch to Apache-2.0 before the repository goes public).
- Publication gate: code is ported, not copied with history; CI runs a secret scan (gitleaks) and a check that no deployment-specific identifiers (IP addresses, phone numbers, host names, email addresses of the original deployment) are present; v0.1.0 is tagged only when CI is green and a clean-VM install following the README works end to end.
