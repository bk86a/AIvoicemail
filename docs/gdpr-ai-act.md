# GDPR and AI Act notes

A checklist for operators. This is not legal advice; the controller (you) remains responsible for
the lawfulness of the processing.

## Transparency to callers

The shipped notices say, in the caller's language, that the call is recorded, that the message is
transcribed and summarised by an automated system using artificial intelligence, and where the
privacy notice is. `aivoicemail check` enforces these elements per language (EU AI Act Art. 50,
GDPR Art. 13 layered notice). Your written privacy notice at `privacy_url` should cover:

- [ ] controller identity and contact details (and DPO, if you have one)
- [ ] purpose: taking messages so that you can call back; legal basis (usually legitimate interest,
      Art. 6(1)(f), or steps prior to a contract, Art. 6(1)(b))
- [ ] categories of data: caller number, recording, transcript, summary, call metadata
- [ ] recipients: your email provider, and each remote STT/LLM provider in your chains (processors)
- [ ] transfers outside the EEA, if any provider processes data there
- [ ] retention (below) and the right to access, erasure, objection and complaint
- [ ] that an AI system produces the transcript and summary

## Retention defaults

- Audio: kept in the spool until the email is accepted by the mail server, processed in memory,
  then deleted. Fallback emails carry the recording as an attachment.
- Call log: metadata only (no transcript or summary), one file per day, deleted after
  `call_log_days` (default 90).
- Asterisk CDR CSV: off by default; when on, rotate it with the same retention.
- Container logs: IDs and outcomes only, size-capped. Exception: Asterisk's SIP stack logs the
  From URI (which can hold a caller number) of a request from an unidentified source when it rejects
  it; the host firewall blocks such sources in normal operation.
- Emails: the recipients' mailboxes are outside aivoicemail - set a retention policy there.

## Processors and data minimisation

What each processor receives: a remote speech-to-text provider gets the recording only; a language
model provider gets the transcript only (the caller number is not sent); the email provider gets the
email (caller number, summary, transcript and, for fallback emails, the recording).

- [ ] sign a data processing agreement with every remote provider in `[stt]` and `[llm]` and with
      your email provider; prefer EU-hosted endpoints
- [ ] for a local-only setup use `chain = ["whisper_local"]` and a local model (Ollama, vLLM)
- [ ] check the licence of each Piper voice you use (voices are downloaded at run time, each with its
      own licence)
- [ ] keep `alert_to` an internal operator address (alerts contain no caller data)
- [ ] record the processing in your register (Art. 30) and assess whether a DPIA is needed

## Security measures you can cite

Inbound-only Asterisk without origination modules or management interfaces, SIP only from the
carrier's ranges, read-only containers without capabilities, secrets only in `.env`, strict spool
protocol in split mode, local speech recognition by default. See [../SECURITY.md](../SECURITY.md).
