# aivoicemail

Self-hosted, GDPR-first AI voicemail. Asterisk answers your number, plays an optional language menu
and a recording/AI transparency notice, and records the message. Local Whisper transcribes it, an
OpenAI-compatible language model (EU-hosted or local) writes a structured summary, and the line's
mailbox receives an email. The audio is deleted once the email has been accepted.

## What you get

- One or more numbers ("lines"), each with its own mailbox, email language and an optional menu of
  up to nine caller languages (templates for Dutch, French, German, English and Polish ship).
- A spoken notice that the call is recorded and processed by an automated system using artificial
  intelligence, with your privacy URL (EU AI Act Art. 50, GDPR Art. 13). `aivoicemail check` refuses
  a notice that loses one of these elements.
- Speech recognition on your own server (faster-whisper), optionally followed by a remote
  OpenAI-compatible fallback.
- Summaries from any OpenAI-compatible endpoint: Mistral, OpenAI, Azure OpenAI, Ollama, vLLM and
  others, with an optional Anthropic adapter. Output is validated against a fixed schema.
- Emails with caller, company, callback number, urgency, requested action and the transcript; a
  missed-call email for silent calls; a fallback email with the recording when a provider chain fails.
- Inbound only and hardened by default: no module that can place calls, no management interfaces,
  SIP accepted only from your carrier's address ranges, read-only containers without capabilities.

## Privacy stance

- Audio stays in the spool until delivery, is processed in memory (tmpfs) and is deleted after the
  mail server accepted the email.
- Transcription runs locally by default; remote providers are opt-in and skipped when their key is
  missing.
- The call log holds metadata only (time, line, caller number, outcome, providers, message ID), is
  rotated daily and deleted after 90 days. Container logs carry IDs and outcomes, never caller numbers.
- Secrets come only from environment variables or a `.env` file.
- A fully local setup (local Whisper plus a local model via Ollama or vLLM) sends nothing to third
  parties except the email itself.

See [docs/gdpr-ai-act.md](docs/gdpr-ai-act.md) for a checklist (not legal advice).

## Quickstart (single host)

Requirements: a Linux server with a public IPv4 address, Docker Engine with Compose v2.24 or later,
an IP-authenticated SIP trunk (DIDWW is documented in [docs/carriers.md](docs/carriers.md)), an SMTP
account, and an API key for your language model (or a local one).

```bash
git clone <repository URL> aivoicemail && cd aivoicemail
cp config/aivoicemail.example.toml config/aivoicemail.toml   # company, trunk, lines, mail
cp .env.example .env && chmod 600 .env                       # secrets
sudo install -d -o 5060 -g 5060 -m 2770 /srv/aivoicemail/spool
mkdir -p generated overrides && sudo chown 10001:5060 generated
docker compose build
./aivm check
./aivm render-prompts
docker compose up -d
./aivm test-call
```

`./aivm` runs the `aivoicemail` command inside the worker image. Before pointing your number at
the server, install the host firewall generated in `generated/nftables/aivoicemail.nft`
([docs/install.md](docs/install.md#host-firewall)). To try everything without any provider account,
start with `AIVOICEMAIL_FAKE_PROVIDERS=1 docker compose up -d` and `./aivm test-call --fake-providers`.

Sizing: Whisper `large-v3` on CPU needs about 4 GB RAM and 4 cores for near-real-time
transcription; use `small` or `medium` on small hosts, or a remote-only STT chain (no model needed).

## Documentation

- [Installation](docs/install.md) - single host, split mode (telephony and processing on two hosts), updates
- [Configuration reference](docs/configuration.md)
- [Carriers](docs/carriers.md)
- [GDPR and AI Act notes](docs/gdpr-ai-act.md)
- [Security policy](SECURITY.md) and [changelog](CHANGELOG.md)
- Design: [docs/superpowers/specs/2026-09-22-aivoicemail-design.md](docs/superpowers/specs/2026-09-22-aivoicemail-design.md)

## Licence

GNU Affero General Public License v3.0 (see [LICENSE](LICENSE)). The images also contain Asterisk
(GPL-2.0), piper-tts (GPL-3.0) and faster-whisper (MIT) with their own licences.
