# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning.

## [0.1.0] - 2026-09-22

### Added

- One TOML config for any number of lines (DID, mailbox, email language, caller-language menu,
  default language, privacy URL); `aivoicemail check` with transparency, voice, secret and `.env`
  checks and optional `--online` provider/SMTP probes.
- `aivoicemail generate`: IP-identified PJSIP trunk, per-line dialplan, CDR switch, nftables ruleset
  and prompt list from the config.
- Prompt templates for Dutch, French, German, English and Polish with enforced AI/recording
  transparency elements; Piper (local) and Azure TTS; `overrides/<prompt>.wav` for recorded prompts.
- Hardened inbound-only Asterisk 20 image with digit-filtered called numbers, `dtmf_mode = auto`
  (RFC 4733 or in-band), 401 for unidentified sources and 404 for unknown numbers.
- Worker: local faster-whisper and OpenAI-compatible STT chain, OpenAI-compatible LLM chain
  (Mistral, OpenAI, Azure OpenAI, Ollama, vLLM) with optional Anthropic adapter, schema validation,
  five-language emails over SMTP, ack after delivery, cached resend, poison-item handling, alerts,
  daily call log with 90-day retention.
- Local and SSH (split mode, `vm-spool` forced command) spool backends.
- Docker Compose files for single-host and split deployments, fake-provider mode, `aivoicemail test-call`.
- CI: pytest, SIPp harness (RFC 4733 and in-band DTMF, injection, 401/404, log privacy), gitleaks,
  identifier blocklist.
