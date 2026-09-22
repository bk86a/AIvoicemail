import re

from conftest import ROOT

AST = ROOT / "asterisk"
FORBIDDEN = re.compile(r"(app_dial|app_originate|res_clioriginate|pbx_spool|app_followme|app_queue|app_page|"
                       r"chan_iax2|res_ari[a-z_]*|res_http_websocket|res_manager[a-z_]*|pbx_lua|pbx_ael|res_agi)\.so")


def conf(name):
    return (AST / "etc" / name).read_text(encoding="utf-8")


def code(text, comment=";"):
    """The file without comment lines (comments may name what the code must never contain)."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(comment))


def loaded():
    return [l.split("=", 1)[1].strip() for l in conf("modules.conf").splitlines() if re.match(r"\s*load\s*=", l)]


def test_autoload_off_and_no_origination_module():
    text = conf("modules.conf")
    assert re.search(r"^autoload\s*=\s*no\s*$", text, re.M)
    assert not re.search(r"^\s*(preload|require)", text, re.M)
    assert [m for m in loaded() if FORBIDDEN.fullmatch(m)] == []


def test_required_modules_loaded():
    for m in ("chan_pjsip.so", "res_pjsip_endpoint_identifier_ip.so", "res_pjsip_authenticator_digest.so",
              "app_record.so", "app_system.so", "app_playback.so", "app_stack.so", "codec_alaw.so", "codec_ulaw.so",
              "format_wav.so", "func_strings.so", "cdr_csv.so", "pbx_config.so"):
        assert m in loaded(), m


def test_cdr_module_switch_is_included_and_required():
    assert conf("modules.conf").rstrip().endswith('#include "modules-cdr.conf"')
    entry = (AST / "bin" / "entrypoint").read_text()
    assert "for f in pjsip-trunk.conf extensions-lines.conf cdr.conf modules-cdr.conf; do" in entry


def test_stasis_stub_is_comment_only():
    lines = [l for l in conf("stasis.conf").splitlines() if l.strip()]
    assert lines and all(l.lstrip().startswith(";") for l in lines)


def test_management_interfaces_disabled():
    assert re.search(r"^enabled\s*=\s*no\s*$", conf("manager.conf"), re.M)
    assert re.search(r"^enabled\s*=\s*no\s*$", conf("http.conf"), re.M)


def test_console_log_never_verbose():
    assert [l for l in conf("logger.conf").splitlines() if l.startswith("console")] == ["console => notice,warning,error"]


def test_pjsip_base_identifies_by_ip_only():
    text = code(conf("pjsip.conf"))
    assert "endpoint_identifier_order = ip" in text and '#include "pjsip-trunk.conf"' in text
    for bad in ("type = auth", "type = registration", "type = aor", "type = endpoint", "anonymous"):
        assert bad not in text, bad


def test_called_number_filtered_to_digits():
    ext = conf("extensions.conf")
    assert "Set(D=${FILTER(0123456789,${VM_RAW})})" in ext
    assert "GotoIf($[${LEN(${D})} = 0 | ${LEN(${D})} != ${LEN(${VM_RAW})}]?reject)" in ext
    assert "same => n(reject),Hangup(1)" in ext
    assert "Set(VM_CID=${FILTER(0123456789+,${CALLERID(num)})})" in ext
    assert '#include "extensions-lines.conf"' in ext


def test_catch_all_extension_rejects_every_other_called_number():
    """A bare "+", a single digit or a non-digit start must exist in from-trunk (else PJSIP answers
    484/404 itself and logs the raw number); `_.`/`_!` would make pbx_config warn at startup."""
    ext = conf("extensions.conf")
    assert "exten => _[\x01-~]!,1,Hangup(1)\n" in ext
    assert not re.search(r"^exten => _[.!],", ext, re.M)


def test_system_arguments_all_single_quoted():
    ext = conf("extensions.conf")
    [call] = re.findall(r"System\((.*)\)$", ext, re.M)
    assert len(re.findall(r"'[^']*'", call)) == 7
    assert re.sub(r"'[^']*'", "", call).split() == ["/usr/local/bin/vm-finalize"]


def test_rtp_range_matches_generator():
    from aivoicemail.generate import RTP_PORTS
    start, end = RTP_PORTS.split("-")
    assert f"rtpstart = {start}" in conf("rtp.conf") and f"rtpend = {end}" in conf("rtp.conf")


def test_entrypoint_and_dockerfile():
    entry = code((AST / "bin" / "entrypoint").read_text(), "#")
    assert entry.endswith("exec /usr/sbin/asterisk -f") and " -v" not in entry
    assert "mkdir -p -m 2770 /srv/aivoicemail/spool/tmp /srv/aivoicemail/spool/ready" in entry
    docker = (AST / "Dockerfile").read_text()
    assert re.search(r"^FROM ubuntu:24\.04@sha256:[0-9a-f]{64}$", docker, re.M)
    assert "ARG ASTERISK_VERSION=1:20.6.0~dfsg+~cs6.13.40431414-2build5" in docker
    assert "USER 5060:5060" in docker


# Asterisk's $[a = b] compares numerically when both operands look like numbers, so an unquoted
# $[${D} = 3220000001] would accept the called number 03220000001. Quoted operands keep their quotes
# in the expression and are compared as strings. Every expression that touches the called number
# must therefore either quote both sides (did-lookup) or only compare its ${LEN(...)} (from-trunk-check).
EXPR = re.compile(r"\$\[([^\[\]]*)\]")
DID_CMP = re.compile(r'"\$\{D\}" = "[0-9]{1,20}"')
NUMBER_VARS = re.compile(r"\$\{(D|VM_RAW|EXTEN[^}]*)\}")


def unsafe_expressions(text):
    bad = []
    for expr in EXPR.findall(text):
        if DID_CMP.fullmatch(expr.strip()):
            continue
        if NUMBER_VARS.search(re.sub(r"\$\{LEN\(\$\{(D|VM_RAW)\}\)\}", "", expr)):
            bad.append(expr)
    return bad


def test_unsafe_expression_checker_catches_numeric_did_compare():
    assert unsafe_expressions("GotoIf($[${D} = 3220000001]?line-be,s,1)") == ["${D} = 3220000001"]
    assert unsafe_expressions('GotoIf($["${D}" = 3220000001]?x)') == ['"${D}" = 3220000001']
    assert unsafe_expressions("GotoIf($[${VM_RAW} != ${D}]?x)") == ["${VM_RAW} != ${D}"]
    assert unsafe_expressions("GotoIf($[${LEN(${EXTEN})} > 0]?x)") == ["${LEN(${EXTEN})} > 0"]


def test_called_number_expressions_are_string_safe():
    from aivoicemail import config, generate
    from conftest import EXAMPLE
    ext = conf("extensions.conf")
    assert EXPR.search(ext) and unsafe_expressions(ext) == []
    for toml in (EXAMPLE, ROOT / "tests" / "golden" / "single" / "config" / "aivoicemail.toml"):
        lines = generate.extensions_lines(config.load(toml))
        assert DID_CMP.search(lines) and unsafe_expressions(lines) == [], toml


def test_did_comparison_in_real_asterisk(tmp_path):
    """Opt-in (AIVOICEMAIL_ASTERISK_IMAGE=aivoicemail-asterisk:test): evaluates the generated did-lookup
    expression form in the built image, including the leading-zero and zero-padded variants."""
    import os
    import subprocess
    import time

    import pytest
    image = os.environ.get("AIVOICEMAIL_ASTERISK_IMAGE")
    if not image:
        pytest.skip("set AIVOICEMAIL_ASTERISK_IMAGE to run against the built image")
    env = ROOT / "tests" / "golden" / "example" / "asterisk"
    tmpfs = [a for d in ("/etc/asterisk", "/var/run/asterisk", "/var/lib/asterisk", "/var/log/asterisk",
                         "/var/spool/asterisk") for a in ("--tmpfs", f"{d}:uid=5060,gid=5060")]
    cid = subprocess.run(["docker", "run", "-d", "--rm", "--network", "none", *tmpfs, "-v", f"{env}:/etc/asterisk-env:ro",
                          image], check=True, capture_output=True, text=True).stdout.strip()

    def cli(cmd):
        return subprocess.run(["docker", "exec", cid, "asterisk", "-rx", cmd], capture_output=True, text=True).stdout

    try:
        for _ in range(60):
            if "Result" in cli("dialplan eval function LEN(x)"):
                break
            time.sleep(0.5)
        # the did-lookup form, with the spaces escaped for the CLI parser
        expr = r'IF($[\"${D}\"\ =\ \"3220000001\"]?match:nomatch)'
        for called, want in (("3220000001", "match"), ("03220000001", "nomatch"), ("003220000001", "nomatch"),
                             ("32200000010", "nomatch"), ("322000000", "nomatch")):
            cli(f"dialplan set global D {called}")
            assert f"Result: {want}" in cli(f"dialplan eval function {expr}"), called
        cli("dialplan set global D 03220000001")
        assert "Result: match" in cli(r"dialplan eval function IF($[${D}\ =\ 3220000001]?match:nomatch)")
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
