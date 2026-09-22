import pytest

from aivoicemail import retry


def test_chain_falls_through_and_skips():
    calls = []

    def boom():
        calls.append("a")
        raise RuntimeError("x")

    def skip():
        calls.append("b")
        raise retry.Skip("no key")

    def ok():
        calls.append("c")
        return 42

    out = retry.chain([("a", boom), ("b", skip), ("c", ok)], sleep=lambda s: None, log=lambda *a: None)
    assert out == (42, "c")
    assert calls == ["a", "a", "a", "b", "c"]


def test_chain_backs_off_between_attempts():
    sleeps = []

    def boom():
        raise RuntimeError("x")

    assert retry.chain([("a", boom)], sleep=sleeps.append, log=lambda *a: None) is None
    assert sleeps == [2, 4]


def test_require_key():
    assert retry.require_key(None, {}) is None
    assert retry.require_key("K", {"K": " v "}) == "v"
    with pytest.raises(retry.Skip):
        retry.require_key("K", {"K": ""})
    with pytest.raises(retry.Skip):
        retry.require_key("K", {})


def test_chain_logs_only_error_type_and_status():
    from aivoicemail import http
    logs = []

    def rejected():
        raise http.ProviderError("HTTP 400", 400, body=b"caller +32470123456 said Goedendag")

    def other():
        raise ValueError("transcript text Goedendag +32470123456")

    retry.chain([("a", rejected), ("b", other)], sleep=lambda s: None, log=logs.append)
    assert "a: attempt 1/3 failed: ProviderError (HTTP 400)" in logs
    assert "b: attempt 1/3 failed: ValueError" in logs
    assert not any("+32470123456" in l or "Goedendag" in l for l in logs)
