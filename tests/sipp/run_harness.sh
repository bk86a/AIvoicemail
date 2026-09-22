#!/usr/bin/env bash
# SIPp harness (CI job "sipp", or locally: bash tests/sipp/run_harness.sh).
# Builds the Asterisk image, starts it on 127.0.0.1:15060 through deploy/compose.telephony.yaml with a
# config generated from tests/sipp/config.toml.in (placeholder prompts), runs every scenario and checks
# spool, container log privacy, CDR, then runs the worker once in fake-provider mode on the spool.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO=$PWD
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT   # replaced by the full cleanup trap once compose is involved
SPOOL=$WORK/spool CDR=$WORK/cdr
mkdir -p "$SPOOL/tmp" "$SPOOL/ready" "$CDR" "$WORK/data" "$WORK/sipp"
# uid 5060 (Asterisk) writes spool and CDR; world-writable test directories let this user check and empty them.
chmod 0777 "$WORK" "$SPOOL" "$SPOOL/tmp" "$SPOOL/ready" "$CDR"
sed -e "s|@REPO@|$REPO|" -e "s|@WORK@|$WORK|" -e "s|@SPOOL@|$SPOOL|" -e "s|@DATA@|$WORK/data|" \
  tests/sipp/config.toml.in > "$WORK/config.toml"
AV=(python3 -m aivoicemail --config "$WORK/config.toml")
export AIVM_GENERATED_DIR="$WORK/generated" AIVM_SPOOL_DIR="$SPOOL" AIVM_CDR_DIR="$CDR"
DC=(docker compose -f deploy/compose.telephony.yaml -p aivm-harness)
BE_DID=3220000001 PL_DID=48320000001 UNKNOWN_DID=3299999999 CALLER=15550100001
UNIDENTIFIED_CALLER=15550100099   # From user of the 401 scenario, the only number PJSIP may log
EXPECT_CALLER=+$CALLER   # vm-finalize prefixes '+' to E.164 numbers delivered without it

cleanup() {
  local rc=$?
  if [ "$rc" != 0 ]; then
    echo "FAILED (rc=$rc); last Asterisk log lines:" >&2
    "${DC[@]}" logs --no-log-prefix --tail 60 asterisk 2>&1 | grep -v "RTP packet" >&2 || true
  fi
  "${DC[@]}" down >/dev/null 2>&1 || true
  rm -rf "$WORK" 2>/dev/null || true
  exit "$rc"
}
trap cleanup EXIT
LOG() { "${DC[@]}" logs --no-log-prefix asterisk 2>&1; }
AX() { "${DC[@]}" exec -T asterisk asterisk -rx "$1"; }
# expect <CLI command> <regex>: capture first (grep -q on a live pipe would break it under pipefail)
expect() {
  local out; out=$(AX "$1")
  grep -qE "$2" <<<"$out" || { echo "expected /$2/ in output of '$1':" >&2; echo "$out" >&2; exit 1; }
}
# wait_ready: until Asterisk answers on the CLI (fully booted)
wait_ready() {
  for _ in $(seq 60); do
    AX "core show version" >/dev/null 2>&1 && AX "core waitfullybooted" >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "Asterisk did not become ready" >&2; exit 1
}

# Clean startup with the default cdr = false: no WARNING/ERROR (cdr_csv is not loaded at all).
sed -i 's/^cdr = true$/cdr = false/' "$WORK/config.toml"
"${AV[@]}" render-prompts        # placeholder voice; also writes generated/asterisk
chmod -R a+rX "$WORK/generated"
"${DC[@]}" up -d --build
wait_ready
if grep -E "WARNING|ERROR|declined" <<<"$(LOG)"; then echo "cdr = false startup log has warnings/errors" >&2; exit 1; fi
expect "module show like cdr_csv" "^0 modules loaded"
"${DC[@]}" down >/dev/null 2>&1
echo "clean startup with cdr = false ok"

# Everything else runs with cdr = true so the CDR rows can be checked.
sed -i 's/^cdr = false$/cdr = true/' "$WORK/config.toml"
"${AV[@]}" generate
chmod -R a+rX "$WORK/generated"
"${DC[@]}" up -d

docker build -q -t aivm-sipp - >/dev/null <<'DOCKERFILE'
FROM ubuntu:24.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends sip-tester \
 && rm -rf /var/lib/apt/lists/*
DOCKERFILE
python3 -m aivoicemail.sipp.timings --sounds "$WORK/generated/sounds/vm" --out "$WORK/sipp" --menu-line be --single-line pl
python3 -m aivoicemail.sipp.pcap --media "$WORK/sipp" --digits 123
chmod -R a+rX "$WORK/sipp"
wait_ready

# Clean startup: every config Asterisk looks for is present and every listed module loads.
STARTUP=$(LOG)
if grep -E "WARNING|ERROR|declined" <<<"$STARTUP"; then echo "startup log has warnings/errors" >&2; exit 1; fi
expect "module show like cdr_csv" "^1 modules loaded"
expect "module show like dial" "^0 modules loaded"
expect "module show like originate" "^0 modules loaded"
MODULES=$(AX "module show")
if grep -qE "^(app_dial|app_originate|res_clioriginate|pbx_spool|app_followme|app_queue|app_page|chan_iax2|res_ari[a-z_]*)\.so" <<<"$MODULES"; then
  echo "forbidden module loaded" >&2; exit 1
fi
# AMI and HTTP are core built-ins: assert they are disabled rather than absent.
expect "manager show settings" "Manager \(AMI\): +No"
expect "http show status" "Server Disabled"
expect "pjsip show endpoint trunk" "^ dtmf_mode +: auto$"
# NAT: public_ip is advertised to the trunk, loopback (test-call) stays local
expect "pjsip show transport transport-udp" "external_media_address +: 203\.0\.113\.10"
expect "pjsip show transport transport-udp" "local_net +: 127\.0\.0\.0/255\.0\.0\.0"
ENDPOINTS=$(AX "pjsip show endpoints")
[ "$(grep -cE "^ Endpoint:  [a-z]" <<<"$ENDPOINTS")" = 1 ] || { echo "expected exactly one endpoint" >&2; exit 1; }
TRANSPORTS=$(AX "pjsip show transports")
grep -qE "transport-udp +udp" <<<"$TRANSPORTS" || { echo "transport-udp missing" >&2; echo "$TRANSPORTS" >&2; exit 1; }
if grep -qiE "tcp|tls" <<<"$TRANSPORTS"; then echo "non-UDP SIP transport configured" >&2; exit 1; fi

# [SIPP_IP=<source>] sipp <scenario> <did> [digit]; extra SIPp arguments in the SIPP_ARGS array
SIPP_ARGS=()
sipp() {
  local scen=$1 did=$2 digit=${3:-} inf=()
  if [ -n "$digit" ]; then
    printf 'SEQUENTIAL\n%s;\n' "$digit" > "$WORK/sipp/field.csv"; chmod 0644 "$WORK/sipp/field.csv"
    inf=(-inf /work/field.csv)
  fi
  echo "=== sipp $scen did=$did ${digit:+digit=$digit}"
  # --pid host: SIPp derives Call-ID, From-tag and Via branch from its PID; as PID 1 in a container
  # every run would reuse them and PJSIP would answer a later BYE from its cached transaction.
  docker run --rm --network host --pid host -v "$WORK/sipp:/work:ro" aivm-sipp \
    sipp 127.0.0.1:15060 -sf "/work/$scen.xml" -s "$did" "${inf[@]}" "${SIPP_ARGS[@]}" \
      -i "${SIPP_IP:-127.0.0.1}" -p 15070 -mi "${SIPP_IP:-127.0.0.1}" -min_rtp_port 26000 -max_rtp_port 26100 \
      -m 1 -timeout 150s -timeout_error -nostdin -trace_err -error_file /dev/stderr >/dev/null
}
check() { python3 tests/sipp/check_spool.py "$SPOOL" "$@"; }

# Menu keys via RFC 4733 (telephone-event offered) and in-band DTMF (PCMA only, as some carriers offer).
for mode in call_rfc4733 call_inband; do
  for pair in 1:nl 2:fr 3:en; do
    sipp "$mode" "$BE_DID" "${pair%%:*}"
    check 1 1 --line be --lang "${pair#*:}" --did "$BE_DID" --caller "$EXPECT_CALLER"
  done
done
sipp call_timeout "$BE_DID";   check 1 1 --line be --lang auto --did "$BE_DID" --caller "$EXPECT_CALLER"
sipp call_single "$PL_DID";    check 1 1 --line pl --lang pl --did "$PL_DID" --caller "$EXPECT_CALLER"
sipp call_hangup "$BE_DID";    check 1 0 --line be --lang auto --did "$BE_DID" --caller "$EXPECT_CALLER"
sipp unknown_did "$UNKNOWN_DID"; check 0 0
# National/international prefixes are not stripped: a leading-zero form of a configured DID is unknown.
for did in "0$BE_DID" "00$BE_DID"; do
  sipp unknown_did "$did"; check 0 0
done

# Called-number injection: percent-encoded quotes, pipes, $(), ';' and backticks must end in 404 from
# the dialplan digit check, leave no spool item and run nothing.
for inj in '9%22%20%7C%20%22%24(touch%20%2Ftmp%2Fpwned)' \
           "${BE_DID}%3Btouch%20%2Ftmp%2Fpwned2" \
           '9%60touch%20%2Ftmp%2Fpwned3%60' \
           "+${BE_DID}%3Btouch%20%2Ftmp%2Fpwned4" \
           "${BE_DID}%22%20%7C%20%22%24(touch%20%2Ftmp%2Fpwned5)"; do
  sipp unknown_did "$inj"; check 0 0
done
# A bare "+", a single digit and letters: the catch-all extension ends them in 404 too (PJSIP would
# otherwise answer 484 for "+" and log the raw number at NOTICE).
for odd in '+' '5' 'zqx' '%1Bzqx'; do
  sipp unknown_did "$odd"; check 0 0
done

# Caller-ID injection: quotes, $(), backticks and ';' in the From user and display name. The call
# proceeds (missed call, no WAV); the spool caller is digits (+ prefixed) or "withheld"; nothing runs.
cid() {
  SIPP_ARGS=(-key cid_user "$1" -key cid_name "$2")
  sipp callerid_injection "$BE_DID"; SIPP_ARGS=()
  check 1 0 --line be --lang auto --did "$BE_DID" --caller "$3"
}
cid "${CALLER}\$(touch%20/tmp/cidpwna)" 'evil\" | $(touch /tmp/cidpwnb) `touch /tmp/cidpwnc`' "$EXPECT_CALLER"
cid '%60touch%20/tmp/cidpwnd%60%22' '$(touch /tmp/cidpwne)' withheld
cid "'%3Btouch%20/tmp/cidpwnf%3B'" "';touch /tmp/cidpwng;'" withheld
MARKERS=$("${DC[@]}" exec -T asterisk sh -c 'ls -d /tmp/pwned* /tmp/cidpwn* 2>/dev/null' || true)
if [ -n "$MARKERS" ]; then
  echo "injection marker file created in the container" >&2; exit 1
fi

# No WARNING/ERROR during the calls (a missing beep prompt would log "Unable to open beep"), and the
# container log never carries caller numbers, injected strings or verbose dialplan lines.
"${DC[@]}" exec -T asterisk test -s /usr/share/asterisk/sounds/en/beep.wav
CALL_LOG=$(LOG)
if grep -E "WARNING|ERROR|beep" <<<"$CALL_LOG"; then echo "Asterisk logged warnings/errors during the calls" >&2; exit 1; fi
# no_leaks <log>: caller numbers, injected strings, the odd called numbers or verbose dialplan lines
no_leaks() {
  if grep -E "$CALLER|touch|pwn|zqx|Executing|to extension" <<<"$1"; then
    echo "caller number, injected string, called number or verbose dialplan line in the container log" >&2; exit 1
  fi
}
no_leaks "$CALL_LOG"

# A source outside every identify section gets 401 and never reaches the dialplan. Checked after the
# log scan: PJSIP logs the unmatched From URI at NOTICE (the host firewall drops such sources in production).
SIPP_IP=127.0.0.2 sipp unidentified "$BE_DID"; check 0 0
if grep -E "WARNING|ERROR" <<<"$(LOG)"; then echo "Asterisk logged warnings/errors for the unidentified call" >&2; exit 1; fi

# CDR: one row per call that reached the dialplan (the 401 never does); accountcode (DID) digits only,
# empty for the malformed numbers, the leading-zero forms kept as dialled.
python3 - "$CDR/Master.csv" "$BE_DID" "$PL_DID" "$UNKNOWN_DID" <<'PY'
import collections, csv, re, sys
path, be, pl, unknown = sys.argv[1:]
rows = list(csv.reader(open(path, newline="")))
assert all(re.fullmatch(r"[0-9]*", r[0]) for r in rows), [r[0] for r in rows]
got = collections.Counter(r[0] for r in rows)
want = collections.Counter({be: 11, pl: 1, unknown: 1, "0" + be: 1, "00" + be: 1, "": 9})
assert got == want, f"CDR accountcodes {dict(got)} != {dict(want)}"
assert all(r[-1] for r in rows), "loguniqueid column missing"
print(f"CDR ok: {len(rows)} rows")
PY
# cdr_csv opens Master.csv per record, so logrotate can rename it and Asterisk recreates it.
mv "$CDR/Master.csv" "$CDR/Master.csv.1"
sipp unknown_did "$UNKNOWN_DID"; check 0 0
sleep 1
[ "$(wc -l < "$CDR/Master.csv")" = 1 ] && [ "$(wc -l < "$CDR/Master.csv.1")" = 24 ] \
  || { echo "Master.csv not recreated after rename" >&2; exit 1; }

# End to end: one more recorded call, then the worker (fake providers) delivers it to the local outbox.
sipp call_rfc4733 "$BE_DID" 2
check 1 1 --line be --lang fr --did "$BE_DID" --caller "$EXPECT_CALLER" --keep
WORKER_OUT=$("${AV[@]}" worker --once --fake-providers 2>&1)
echo "$WORKER_OUT"
if grep -E "$CALLER" <<<"$WORKER_OUT"; then echo "caller number in worker output" >&2; exit 1; fi
python3 - "$WORK" <<'PY'
import sys
from pathlib import Path
from aivoicemail.calllog import read_entries
from aivoicemail.fake.smtp_capture import messages
work = Path(sys.argv[1])
[msg] = messages(work / "data" / "outbox")
assert msg["To"] == "info@example.com", msg["To"]
assert msg["Subject"].startswith("[Voicemail] low - +15550100001"), msg["Subject"]
assert "Transcript (fr):" in msg.get_content()
[entry] = read_entries(work / "data" / "calllog")
assert entry["outcome"] == "message" and entry["did"] == "3220000001" and entry["language"] == "fr", entry
assert not list((work / "spool" / "ready").iterdir())
print("worker end-to-end ok")
PY

# Final scan of the whole container log after every call: nothing above may have leaked later on.
# Only the 401 call's NOTICE (its own distinct From user) is exempt.
FINAL_LOG=$(LOG | grep -v "$UNIDENTIFIED_CALLER" || true)
if grep -E "WARNING|ERROR" <<<"$FINAL_LOG"; then echo "Asterisk logged warnings/errors" >&2; exit 1; fi
no_leaks "$FINAL_LOG"
echo "ALL SIPP HARNESS CHECKS PASSED"
