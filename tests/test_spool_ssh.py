import io
import subprocess
import tarfile

import pytest

from aivoicemail.spool import Item, SpoolError
from aivoicemail.spool import ssh

ID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def tar_of(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_parse_list():
    assert ssh.parse_list(f"{ID} 1700000000 1\n") == [Item(ID, 1700000000, True)]


def test_parse_list_rejects_garbage():
    with pytest.raises(SpoolError):
        ssh.parse_list("rm -rf 1 1\n")


def test_extract_ok(tmp_path):
    meta, wav = ssh.extract(tar_of({f"{ID}.json": b'{"id": "%s"}' % ID.encode(), f"{ID}.wav": b"RIFF"}), ID, tmp_path)
    assert meta["id"] == ID and wav.read_bytes() == b"RIFF"


def test_extract_json_only(tmp_path):
    assert ssh.extract(tar_of({f"{ID}.json": b'{"id": "%s"}' % ID.encode()}), ID, tmp_path)[1] is None


def test_extract_rejects_unexpected_member(tmp_path):
    with pytest.raises(SpoolError):
        ssh.extract(tar_of({f"{ID}.json": b"{}", "../evil": b"x"}), ID, tmp_path)


def test_extract_rejects_symlink_member(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        info = tarfile.TarInfo(f"{ID}.json")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        t.addfile(info)
    with pytest.raises(SpoolError):
        ssh.extract(buf.getvalue(), ID, tmp_path)


def test_extract_rejects_mismatched_meta_id(tmp_path):
    other = "11111111-2222-4333-8444-555555555555"
    with pytest.raises(ValueError):
        ssh.extract(tar_of({f"{ID}.json": b'{"id": "%s"}' % other.encode()}), ID, tmp_path)


def test_extract_without_metadata(tmp_path):
    with pytest.raises(SpoolError):
        ssh.extract(tar_of({f"{ID}.wav": b"RIFF"}), ID, tmp_path)


def test_commands_and_ssh_options(tmp_path):
    calls = []

    def runner(args, capture_output, timeout):
        calls.append(args)
        out = {"list": f"{ID} 1 0\n".encode(), f"get {ID}": tar_of({f"{ID}.json": b'{"id": "%s"}' % ID.encode()}),
               f"ack {ID}": b""}[args[-1]]
        return subprocess.CompletedProcess(args, 0, out, b"")

    sp = ssh.SshSpool("aivm-spool@192.0.2.20", tmp_path / "key", tmp_path / "known_hosts", runner=runner)
    assert sp.list() == [Item(ID, 1, False)]
    assert sp.get(ID, tmp_path / "w")[0]["id"] == ID
    sp.ack(ID)
    first = calls[0]
    assert first[0] == "ssh" and "BatchMode=yes" in first and "StrictHostKeyChecking=yes" in first
    assert f"UserKnownHostsFile={tmp_path / 'known_hosts'}" in first
    assert first[-2:] == ["aivm-spool@192.0.2.20", "list"]


def test_nonzero_exit_is_spool_error(tmp_path):
    runner = lambda args, capture_output, timeout: subprocess.CompletedProcess(args, 255, b"", b"refused")
    with pytest.raises(SpoolError):
        ssh.SshSpool("t", tmp_path / "k", tmp_path / "h", runner=runner).list()


def test_timeout_is_spool_error(tmp_path):
    def runner(args, capture_output, timeout):
        raise subprocess.TimeoutExpired(args, timeout)
    with pytest.raises(SpoolError):
        ssh.SshSpool("t", tmp_path / "k", tmp_path / "h", runner=runner).list()


def test_oserror_from_runner_is_spool_error(tmp_path):
    """subprocess.run itself can raise OSError (e.g. fork/exec failure, ssh binary missing) rather
    than returning a CompletedProcess - that must not escape as a bare OSError."""
    def runner(args, capture_output, timeout):
        raise OSError("fork failed")
    with pytest.raises(SpoolError):
        ssh.SshSpool("t", tmp_path / "k", tmp_path / "h", runner=runner).list()


def test_orphans(tmp_path):
    runner = lambda args, capture_output, timeout: subprocess.CompletedProcess(args, 0, b"2\n", b"")
    assert ssh.SshSpool("t", tmp_path / "k", tmp_path / "h", runner=runner).orphans() == 2
    bad = lambda args, capture_output, timeout: subprocess.CompletedProcess(args, 0, b"lots\n", b"")
    with pytest.raises(SpoolError):
        ssh.SshSpool("t", tmp_path / "k", tmp_path / "h", runner=bad).orphans()
