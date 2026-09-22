# Installation

Two layouts are supported:

- **Single host** - Asterisk and the worker on one server (`compose.yaml` in the repository root).
- **Split mode** - Asterisk on a small SIP host, the worker on a bigger processing host that reads the
  spool over SSH (`deploy/compose.telephony.yaml` and `deploy/compose.worker.yaml`).

Every command below runs from the repository root. `./aivm <command>` runs `aivoicemail <command>` in
the worker image: the `tools` service (host networking, full container hardening) for every command
except `test-call`, which runs in a separate `testcall` service - it alone is granted `cap_add:
[NET_RAW]` and has `no-new-privileges` cleared, because SIPp's RTP port range checks open a raw
socket via a file capability set on `/usr/bin/sipp` at build time; `tools` and `worker` never get
either. Secrets reach every service only through `env_file: [../.env]`, never through inline
`environment:` values (`AIVOICEMAIL_FAKE_PROVIDERS` is the only plain environment variable set in
the compose files, and it carries no secret).

## Requirements

- Linux (x86_64 or arm64) with Docker Engine and Compose v2.24 or later.
- A public IPv4 address reachable by your carrier on UDP 5060 and UDP 10000-20000.
- An IP-authenticated SIP trunk; see [carriers.md](carriers.md).
- An SMTP account that may send from the configured `from` address.
- For summaries: an OpenAI-compatible endpoint and key (Mistral, OpenAI, Azure OpenAI) or a local
  server (Ollama, vLLM).
- Sizing: Whisper `large-v3` on CPU needs about 4 GB RAM and 4 cores for near-real-time; `small` or
  `medium` on smaller hosts; a remote-only STT chain needs no model.

## Single host

1. **Get the code and configure.**

   ```bash
   git clone <repository URL> aivoicemail && cd aivoicemail
   cp config/aivoicemail.example.toml config/aivoicemail.toml
   cp .env.example .env && chmod 600 .env
   ```

   Edit `config/aivoicemail.toml` ([configuration.md](configuration.md)): company name and privacy
   URL, trunk ranges and `public_ip`, one `[[line]]` per number, mail server, providers. Put the
   secrets named by `*_env` keys into `.env`. `./aivm` warns if `.env` is world-readable; a value
   containing `$` must be single-quoted (`KEY='pa$$word'`), because Compose auto-loads this same
   file for its own `${VAR}` interpolation and would otherwise try to expand it.

2. **Create the host directories.** The spool is shared by Asterisk (uid 5060) and the worker
   (uid 10001, group 5060); `generated/` is written by the `tools` service.

   ```bash
   sudo install -d -o 5060 -g 5060 -m 2770 /srv/aivoicemail/spool
   mkdir -p generated overrides && sudo chown 10001:5060 generated
   ```

3. **Build and check.**

   ```bash
   docker compose build
   ./aivm check            # add --online to contact the providers and the SMTP server
   ```

   Fix every `ERROR`. `WARNING`s are advisory (for example a missing optional provider key).

4. **Render the prompts** (and the Asterisk files generated from the config). Piper voices are
   downloaded once into the `data` volume. To use recorded human prompts instead, put
   `overrides/<prompt>.wav` (8 kHz mono 16-bit) in place; `generated/prompts.txt` lists the names.

   ```bash
   ./aivm render-prompts
   ```

5. **Install the host firewall** - see [Host firewall](#host-firewall) below.

6. **Start and test.**

   ```bash
   docker compose up -d
   ./aivm test-call
   ```

   `test-call` calls your first line from the server itself (menu key 1 as in-band DTMF) and waits
   for the worker's outcome. A tone is not speech, so a real setup reports `outcome=missed` and sends
   a missed-call email to the line's mailbox; pass `--wav speech.wav` (8 kHz mono 16-bit, up to 20 s)
   to exercise transcription and summary. Then call the number from a phone.

### Trying it without provider accounts

```bash
./aivm render-prompts --engine placeholder
AIVOICEMAIL_FAKE_PROVIDERS=1 docker compose up -d
./aivm test-call --fake-providers
```

The worker then uses a fixed transcript and summary and delivers emails to a local capture in the
`data` volume (`/var/lib/aivoicemail/outbox`) instead of your SMTP server. Nothing leaves the host.
Restart without the variable (`docker compose up -d`) to go live.

## Host firewall

Asterisk uses host networking, so the host firewall decides who can reach SIP and RTP.
`aivoicemail generate` (also run by `render-prompts`) writes `generated/nftables/aivoicemail.nft`:
its own `inet aivoicemail` table with policy drop that admits loopback, established traffic, ICMP,
DHCP and DHCPv6 replies, the ports in `[firewall]` (SSH 22 by default) and SIP/RTP only from the trunk ranges.
Other tables and chains are left untouched, but because every input chain must accept a packet,
anything else the host serves must be listed under `[firewall]`.

Apply it with a timed rollback so a mistake cannot lock you out:

```bash
sudo systemd-run --unit=aivm-nft-rollback --on-active=120 /usr/sbin/nft delete table inet aivoicemail
sudo nft -f generated/nftables/aivoicemail.nft
# open a NEW SSH session now; if it works, keep the rules:
sudo systemctl stop aivm-nft-rollback.timer
```

Load it at boot:

```bash
sudo install -D -m 0644 generated/nftables/aivoicemail.nft /etc/aivoicemail/aivoicemail.nft
sudo install -m 0644 deploy/nftables/aivoicemail-nft.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now aivoicemail-nft
```

**Cloud firewalls / security groups:** allow UDP 5060 and UDP 10000-20000 only from the trunk's
ranges. When the provider's rule quota is small, merge them into one rule per range covering UDP
5060-20000; the host firewall still limits SIP to 5060.

### Local language model (Ollama)

The worker runs on a Docker bridge network, so `127.0.0.1` inside it is the container, not the host.
Point the endpoint at the host through the `host.docker.internal` alias the worker service defines
(`extra_hosts: ["host.docker.internal:host-gateway"]`):

```toml
[llm]
chain = ["local"]
local = { base_url = "http://host.docker.internal:11434/v1", model = "<model>", structured = "json_object" }
```

Make Ollama listen on the Docker bridge address, e.g. `OLLAMA_HOST=172.17.0.1:11434` (the `docker0`
address, `ip -4 addr show docker0`), not only on `127.0.0.1`. Bridge traffic to the host passes the
host firewall's input chain, and a `[firewall] allow_tcp` entry is not the right fix: it would open the
port to the whole internet. Instead `generate` adds `iifname "docker0"` and `iifname "br-*"` rules for
the port of every `[stt]`/`[llm]` endpoint whose host is `host.docker.internal` (port 80/443 when the
URL has none); re-run `./aivm generate` and re-apply the ruleset after changing the endpoint.

## Optional CDR

With `[retention] cdr = true` Asterisk writes a CSV call record per call to `/srv/aivoicemail/cdr`:

```bash
sudo install -d -o 5060 -g 5060 -m 2770 /srv/aivoicemail/cdr
sudo install -m 0644 deploy/logrotate/aivoicemail-cdr /etc/logrotate.d/aivoicemail-cdr
```

Keep `rotate`/`maxage` in that file equal to `call_log_days`.

## Split mode

The telephony host runs Asterisk only; the processing host runs the worker and pulls the spool
through a restricted SSH key bound to the `vm-spool` forced command.

**Telephony host** (use the same `config/aivoicemail.toml` on both hosts):

```bash
sudo groupadd -g 5060 aivm
sudo useradd -r -g aivm -m -d /var/lib/aivm-spool -s /bin/sh aivm-spool
sudo install -d -o 5060 -g 5060 -m 2770 /srv/aivoicemail/spool
sudo install -m 0755 aivoicemail/spool/vm_spool.py /usr/local/bin/vm-spool
mkdir -p generated overrides && sudo chown 10001:5060 generated
touch .env && chmod 600 .env
AIVM_COMPOSE=deploy/compose.worker.yaml ./aivm render-prompts   # builds the worker image once; no worker runs here
docker compose -f deploy/compose.telephony.yaml up -d --build
```

Add `[firewall] allow_tcp = [22]` (and restrict SSH to the processing host in your cloud firewall).

**Processing host:**

```bash
mkdir -p secrets
ssh-keygen -t ed25519 -N '' -C aivoicemail-worker -f secrets/spool_key
ssh-keyscan -t ed25519 <telephony host> > secrets/known_hosts
sudo chown 10001:5060 secrets/spool_key secrets/known_hosts && sudo chmod 0400 secrets/spool_key
```

Compare the pinned host key with the telephony host's own
(`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` there, `ssh-keygen -lf secrets/known_hosts` here)
before trusting it. On the telephony host, append the public key to
`/var/lib/aivm-spool/.ssh/authorized_keys` (mode 0600, owned by `aivm-spool`) as a single line:

```
restrict,from="<processing host IP>",command="/usr/local/bin/vm-spool" ssh-ed25519 AAAA... aivoicemail-worker
```

Configure the SSH spool on the processing host and start the worker:

```toml
[spool]
backend = "ssh"
ssh_target = "aivm-spool@<telephony host>"
ssh_key = "secrets/spool_key"
known_hosts = "secrets/known_hosts"
```

```bash
AIVM_COMPOSE=deploy/compose.worker.yaml ./aivm check
docker compose -f deploy/compose.worker.yaml up -d --build
```

`vm-spool` accepts only `list`, `get <uuid>` and `ack <uuid>`; the worker accepts only
`<uuid>.json` and `<uuid>.wav` regular files from it. `test-call` needs Asterisk on the same host,
so in split mode verify with a real call.

## Updates

```bash
git pull && docker compose up -d --build
```

The spool persists; items in flight are picked up again. Re-run `./aivm check` and
`./aivm render-prompts` when the release notes mention prompt or config changes (in split mode also
re-install `vm-spool` on the telephony host). A worker restart between sending an email and removing
its item from the spool can resend that one email after the restart.

## Troubleshooting

- `docker compose logs asterisk` shows notice-level lines only (by design no caller numbers). For SIP
  traces enable them temporarily: `docker compose exec asterisk asterisk -rx "pjsip set logger on"`;
  they contain caller numbers, so turn them off again and clear the log.
- `docker compose logs worker` shows one line per item: `<id> <line> <outcome> msg=<message id>`.
- Menu keys ignored: see the telephone-event note in [carriers.md](carriers.md).
- Alerts (stale items, unreachable spool, stale orphan audio in the spool's `tmp/` or `ready/`,
  items that failed repeatedly) go to `[mail] alert_to`, at most one per condition per hour. Orphaned
  files are never deleted automatically: inspect and remove them on the telephony host.
