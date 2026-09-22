import os

import pytest

from aivoicemail.spool import Item, SpoolError, build_spool
from aivoicemail.spool.local import LocalSpool

ID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def make(root, meta=None, wav=b"RIFFdata", mtime=1_700_000_000):
    ready = root / "ready"
    ready.mkdir(parents=True, exist_ok=True)
    (ready / f"{ID}.json").write_text(meta if meta is not None else '{"id": "%s", "line": "be"}' % ID)
    if wav is not None:
        (ready / f"{ID}.wav").write_bytes(wav)
    os.utime(ready / f"{ID}.json", (mtime, mtime))


def test_list_get_ack(tmp_path):
    make(tmp_path / "spool")
    sp = LocalSpool(tmp_path / "spool")
    assert sp.list() == [Item(ID, 1_700_000_000, True)]
    meta, wav = sp.get(ID, tmp_path / "work")
    assert meta["line"] == "be" and wav.read_bytes() == b"RIFFdata" and wav.parent == tmp_path / "work"
    sp.ack(ID)
    assert sp.list() == []
    sp.ack(ID)


def test_json_only_item(tmp_path):
    make(tmp_path / "spool", wav=None)
    sp = LocalSpool(tmp_path / "spool")
    assert sp.list()[0].has_audio is False
    assert sp.get(ID, tmp_path / "work")[1] is None


def test_missing_directory_is_spool_error(tmp_path):
    with pytest.raises(SpoolError):
        LocalSpool(tmp_path / "missing").list()


def test_vanished_item_is_spool_error(tmp_path):
    (tmp_path / "spool" / "ready").mkdir(parents=True)
    with pytest.raises(SpoolError):
        LocalSpool(tmp_path / "spool").get(ID, tmp_path / "work")


def test_mismatched_meta_id_is_content_error(tmp_path):
    make(tmp_path / "spool", meta='{"id": "11111111-2222-4333-8444-555555555555"}')
    with pytest.raises(ValueError):
        LocalSpool(tmp_path / "spool").get(ID, tmp_path / "work")


def test_symlinked_wav_is_content_error(tmp_path):
    make(tmp_path / "spool", wav=None)
    (tmp_path / "other.wav").write_bytes(b"x")
    (tmp_path / "spool" / "ready" / f"{ID}.wav").symlink_to(tmp_path / "other.wav")
    with pytest.raises(ValueError):
        LocalSpool(tmp_path / "spool").get(ID, tmp_path / "work")


def test_bad_ids_rejected(tmp_path):
    make(tmp_path / "spool")
    sp = LocalSpool(tmp_path / "spool")
    for bad in ("../x", ID.upper(), ID + "\n"):
        with pytest.raises(SpoolError):
            sp.get(bad, tmp_path / "work")
        with pytest.raises(SpoolError):
            sp.ack(bad)


def test_build_spool_local(cfg):
    assert isinstance(build_spool(cfg), LocalSpool)
