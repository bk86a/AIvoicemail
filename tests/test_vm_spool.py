import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "aivoicemail" / "spool" / "vm_spool.py"
ID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def run(root, command, *, via_ssh=True):
    env = {"VM_SPOOL": str(root), "PATH": os.environ["PATH"]}
    args = [sys.executable, str(SCRIPT)]
    if via_ssh:
        env["SSH_ORIGINAL_COMMAND"] = command
    else:
        args += command.split()
    return subprocess.run(args, env=env, capture_output=True)


def make(root, ident=ID, wav=True, mtime=1_700_000_000):
    ready = root / "ready"
    ready.mkdir(parents=True, exist_ok=True)
    (ready / f"{ident}.json").write_text('{"id": "%s"}' % ident)
    if wav:
        (ready / f"{ident}.wav").write_bytes(b"RIFF....")
    os.utime(ready / f"{ident}.json", (mtime, mtime))


def test_list(tmp_path):
    make(tmp_path)
    make(tmp_path, "1f8fad5b-d9cb-469f-a165-70867728950e", wav=False, mtime=1_600_000_000)
    r = run(tmp_path, "list")
    assert r.returncode == 0
    assert r.stdout.decode().splitlines() == [
        "1f8fad5b-d9cb-469f-a165-70867728950e 1600000000 0",
        f"{ID} 1700000000 1",
    ]


def test_list_missing_spool_fails(tmp_path):
    assert run(tmp_path / "nope", "list").returncode == 4


def test_list_skips_symlinked_metadata(tmp_path):
    (tmp_path / "ready").mkdir()
    (tmp_path / "secret.json").write_text("{}")
    (tmp_path / "ready" / f"{ID}.json").symlink_to(tmp_path / "secret.json")
    assert run(tmp_path, "list").stdout == b""


def test_get_returns_tar(tmp_path):
    make(tmp_path)
    r = run(tmp_path, f"get {ID}")
    assert r.returncode == 0
    names = tarfile.open(fileobj=io.BytesIO(r.stdout)).getnames()
    assert sorted(names) == [f"{ID}.json", f"{ID}.wav"]


def test_get_unknown(tmp_path):
    (tmp_path / "ready").mkdir()
    assert run(tmp_path, f"get {ID}").returncode == 3


def test_ack_deletes_and_is_idempotent(tmp_path):
    make(tmp_path)
    assert run(tmp_path, f"ack {ID}").returncode == 0
    assert list((tmp_path / "ready").iterdir()) == []
    assert run(tmp_path, f"ack {ID}").returncode == 0


def test_rejects_other_commands_and_bad_ids(tmp_path):
    make(tmp_path)
    for cmd in ["", "sh", "list; rm -rf /", f"get ../{ID}", "get ../../etc/passwd",
                f"ack {ID} extra", "get 0F8FAD5B-D9CB-469F-A165-70867728950E", "scp -f x", f"get {ID}\n"]:
        r = run(tmp_path, cmd)
        assert r.returncode == 2, cmd
        assert b"rejected" in r.stderr
    assert (tmp_path / "ready" / f"{ID}.json").exists()


def test_argv_mode_for_local_use(tmp_path):
    make(tmp_path)
    assert run(tmp_path, "list", via_ssh=False).returncode == 0
