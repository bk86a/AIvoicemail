# Configuration reference

One TOML file, `config/aivoicemail.toml` (start from `config/aivoicemail.example.toml`). Secrets are
never written here: keys ending in `_env` name environment variables, set in `.env` (mode 0600) or
the process environment - a value containing `$` must be single-quoted (`KEY='pa$$word'`), since
Compose also reads this file for its own `${VAR}` interpolation. Relative paths resolve against the
install root (the directory above `config/`; `/opt/aivoicemail` inside the containers). Run
`./aivm check` after every change and `./aivm render-prompts` when lines, languages, texts or voices
change.

## `[company]`

- `name` - spoken in the menu and notices, e.g. `"ACME BV"`.
- `privacy_url_default` - privacy notice URL spoken in every notice unless a line sets its own.

## `[trunk]`

IP-authenticated trunks only in v0.1: SIP is accepted from these ranges, everything else gets 401.

- `provider` - label only.
- `signalling_ranges` - CIDRs the carrier sends SIP from (identify sections and firewall).
- `media_ranges` - CIDRs the carrier sends RTP from (firewall); default: `signalling_ranges`.
- `public_ip` - external address for SIP and SDP when the host is behind NAT.
- `local_net` - the host's local network behind NAT (optional).
- `bind` - SIP UDP listen address, default `"0.0.0.0:5060"`.
- `media_address` - bind RTP to this local address (optional).
- `allow_local_test` - identify `127.0.0.1` as the trunk so `aivoicemail test-call` can call in from
  the host itself; default `true`.

## `[[line]]` (one per number)

- `id` - lowercase letter plus up to 15 lowercase letters/digits; used in prompt names (`<id>-menu`).
- `did` - the number as the carrier delivers it: E.164 digits without `+`, unique.
- `mailbox` - where this line's emails go.
- `email_language` - language of the email labels and of the summary: `en`, `nl`, `fr`, `de`, `pl`.
- `menu` - caller languages; one entry means no menu, 2-9 entries play "press 1, 2, ...".
- `default_language` - with a menu, what happens when no key is pressed: `"auto"` (default) plays a
  short notice in every menu language and detects the language from the speech, or one of the menu
  languages.
- `privacy_url` - optional per-line privacy URL.

## `[recording]`

- `max_seconds` - maximum message length, default 180.
- `silence_seconds` - stop after this much silence, default 5.
- `min_speech_seconds` - less detected speech sends a missed-call email instead, default 2.

## `[stt]`

- `chain` - providers in order; `"whisper_local"` is the local engine, other names refer to
  endpoint tables below. Default `["whisper_local"]`.
- `whisper_model` - faster-whisper model: `large-v3` (default), `medium`, `small`, or a local path.
- `whisper_threads` - CPU threads, default 4.
- Any other key is an endpoint table, e.g.
  `remote = { base_url = "https://api.mistral.ai/v1", model = "voxtral-mini-latest", key_env = "STT_API_KEY" }`,
  called as `POST <base_url>/audio/transcriptions`.

## `[llm]`

- `chain` - endpoint names in order; each is tried up to three times, invalid output counts as a failure.
- Endpoint tables (also used by `[stt]`):
  - `kind` - `"openai_compatible"` (default) or `"anthropic"` (LLM only; needs `pip install aivoicemail[anthropic]`,
    included in the image).
  - `base_url` - API root, e.g. `https://api.mistral.ai/v1`, `https://<resource>.openai.azure.com/openai/v1`,
    `http://host.docker.internal:11434/v1` (Ollama on the Docker host; see
    [install.md](install.md#local-language-model-ollama)).
  - `model` - model or deployment name.
  - `key_env` - environment variable holding the key; omit for keyless local servers. A named key
    that is not set skips the provider.
  - `structured` - `"json_schema"` (default; strict schema), `"json_object"` (JSON mode) or `"none"`
    (plain text; the first JSON object is extracted). Output is always normalised and validated.
  - `auth` - `"bearer"` (default) or `"api-key"` (Azure OpenAI key header).

## `[mail]`

- `smtp_host`, `smtp_port` (default 587).
- `smtp_security` - `"starttls"` (default), `"tls"` (implicit, default for port 465) or `"none"`
  (only for a relay on the same host).
- `smtp_user_env`, `smtp_password_env` - environment variables with the SMTP login (set both or neither).
- `from`, `from_name` - sender of every email.
- `alert_to` - operator address for alerts (stale items, unreachable spool, items that failed
  repeatedly). Alerts never contain caller data.

## `[tts]`

- `engine` - `"piper"` (default, local), `"azure"` or `"placeholder"` (beeps sized like speech; tests).
- `voices` - voice per language, e.g. `{ nl = "nl_BE-nathalie-medium" }` (Piper) or
  `{ nl = "nl-BE-DenaNeural" }` (Azure). Every menu language needs one.
- `pronunciation` - IPA per word and language, e.g. `{ "ACME" = { en = "ˈæk.mi" } }`; Azure receives
  `<phoneme>` tags, Piper raw phonemes.
  Piper voices are downloaded at run time and each voice carries its own licence (see the voice's
  model card); check it before use.
- `sentence_ms` - pause between sentences of the notices, default 300.
- `azure` - `{ region = "westeurope", key_env = "AZURE_SPEECH_KEY" }` for `engine = "azure"`.

## `[retention]`

- `call_log_days` - call log retention in days, default 90 (one file per day, pruned by the worker).
- `cdr` - also write an Asterisk CDR CSV, default `false` (see [install.md](install.md#optional-cdr)).

## `[worker]` (optional)

- `poll_seconds` - spool poll interval, default 30.
- `stale_minutes` - alert when an item waits longer, default 60. Independently the worker alerts
  ("stale orphan") on orphaned audio older than 60 minutes: files left in the spool's `tmp/` or WAVs
  in `ready/` without metadata. They are never deleted automatically.
- `unreachable_minutes` - alert when the spool cannot be listed for longer, default 15.
- `max_failures` - consecutive failures before an item is sent as a fallback and removed, default 5.

## `[spool]` (optional)

- `backend` - `"local"` (default, shared directory) or `"ssh"` (split mode).
- `path` - spool directory for `local`, default `/srv/aivoicemail/spool`.
- `ssh_target`, `ssh_key`, `known_hosts` - for `ssh`, see [install.md](install.md#split-mode).

## `[firewall]` (optional, used by `generate`)

- `allow_tcp` - TCP ports the host firewall admits from anywhere, default `[22]`.
- `allow_udp` - UDP ports admitted from anywhere, default `[]`.

## `[paths]` (optional)

- `data_dir` - call log, models, voices, fake outbox; default `/var/lib/aivoicemail`.
- `work_dir` - per-item scratch (tmpfs), default `/run/aivoicemail`.
- `prompts_dir` - prompt templates, default `prompts`.
- `overrides_dir` - recorded prompt WAVs, default `overrides`.
- `generated_dir` - generated Asterisk files, firewall and sounds, default `generated`.

## `[prompt_text.<lang>]` (optional)

Overrides any template text of `prompts/<lang>.toml`: `menu_option` (with `{digit}`), `notice`,
`notice_short`, `after_tone`, `thanks`, `thanks_short`; `{company}` and `{privacy_url}` are filled
in. `check` and `render-prompts` refuse a notice that no longer contains the template's
`transparency` phrases (recorded, automated system, artificial intelligence, privacy URL).

## Prompt names

`generated/prompts.txt` lists the prompts of your config: `<id>-menu`, `<id>-notice-<lang>`,
`<id>-notice-auto`, `<id>-thanks-<lang>`, `<id>-thanks-auto` for menu lines;
`<id>-notice-<lang>` and `<id>-thanks-<lang>` for single-language lines. A file
`overrides/<name>.wav` (8 kHz mono 16-bit) replaces the rendered prompt; you are then responsible
for the recording containing the transparency elements.
