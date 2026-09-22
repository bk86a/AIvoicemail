import pytest

from aivoicemail import processor
from aivoicemail.alerts import Alerter
from aivoicemail.calllog import CallLog, read_entries
from aivoicemail.mail import SendError
from aivoicemail.processor import Deps, Processor
from aivoicemail.spool import Item, SpoolError

ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
ID2 = "11111111-2222-4333-8444-555555555555"
GOOD = {"caller_name": "Jan", "company": None, "subject": "Offerte", "callback_number": None,
        "language": "nl", "urgency": "normal", "summary": "Vraagt offerte.", "requested_action": None}


class FakeSpool:
    def __init__(self, meta, wav=b"RIFFxxxx", ack_error=None):
        self.meta, self.wav, self.acked, self.ack_error, self.gets = meta, wav, [], ack_error, 0

    def get(self, item_id, dest):
        self.gets += 1
        dest.mkdir(parents=True, exist_ok=True)
        wav_path = None
        if self.wav is not None:
            wav_path = dest / f"{item_id}.wav"
            wav_path.write_bytes(self.wav)
        return dict(self.meta, id=item_id), wav_path

    def ack(self, item_id):
        if self.ack_error:
            raise self.ack_error
        self.acked.append(item_id)


class FakeSpeech:
    def __init__(self, speech=10.0):
        self.speech = speech

    def speech_seconds(self, p):
        return self.speech


class BrokenSpeech:
    def speech_seconds(self, p):
        raise RuntimeError("onnx exploded")


def meta(**kw):
    m = {"id": ID, "line": "be", "did": "3220000001", "caller": "+32470123456", "lang_choice": "nl",
         "started_at": "2026-09-13T09:00:00Z", "ended_at": "2026-09-13T09:00:30Z", "duration_s": 30, "has_audio": True}
    m.update(kw)
    return m


def unexpected(*a, **k):
    raise AssertionError("provider chain must not be called in this test")


def ok_transcribe(wav, lang, candidates):
    return "Goedendag", "whisper_local"


def ok_summarise(transcript, meta, email_language):
    return GOOD, "primary"


def make(cfg, sp, sends, *, speech=None, send_error=None, logs=None, transcribe=unexpected, summarise=unexpected):
    def send(**kw):
        if send_error:
            raise send_error
        sends.append(kw)
        return "MID"

    log = logs.append if logs is not None else (lambda m: None)
    d = Deps(cfg=cfg, spool=sp, speech=speech or FakeSpeech(), transcribe=transcribe, summarise=summarise,
             send=send, calllog=CallLog(cfg.paths.data_dir / "calllog", 90, log=log), log=log, clock=lambda: 1000.0)
    d.alerter = Alerter(send=lambda **kw: d.send(**kw), alert_to=cfg.mail.alert_to, log=log)
    return Processor(d)


def calls(cfg):
    return read_entries(cfg.paths.data_dir / "calllog")


def test_happy_path_sends_then_acks(cfg):
    sends, sp = [], FakeSpool(meta())
    p = make(cfg, sp, sends, transcribe=ok_transcribe, summarise=ok_summarise)
    assert p.process(Item(ID, 0, True)) == "acked"
    assert sp.acked == [ID]
    assert sends[0]["to"] == "info@acme.example" and sends[0]["attachments"] == ()
    assert sends[0]["subject"] == "[Voicemail] normal - Jan - Offerte"
    assert not (cfg.paths.work_dir / ID).exists()


def test_chains_get_line_candidates_and_email_language(cfg):
    seen = {}

    def transcribe(wav, lang, candidates):
        seen["t"] = (lang, candidates)
        return "Goedendag", "whisper_local"

    def summarise(transcript, m, email_language):
        seen["s"] = (transcript, email_language)
        return GOOD, "primary"

    make(cfg, FakeSpool(meta(lang_choice="auto")), [], transcribe=transcribe, summarise=summarise).process(Item(ID, 0, True))
    assert seen == {"t": ("auto", ("nl", "fr", "en")), "s": ("Goedendag", "en")}


def test_missed_call_json_only(cfg):
    sends = []
    assert make(cfg, FakeSpool(meta(has_audio=False), wav=None), sends).process(Item(ID, 0, False)) == "acked"
    assert sends[0]["subject"].startswith("[Voicemail] Missed call")


def test_short_speech_is_missed_call(cfg):
    sends = []
    assert make(cfg, FakeSpool(meta()), sends, speech=FakeSpeech(1.2)).process(Item(ID, 0, True)) == "acked"
    assert "Missed call" in sends[0]["subject"]


def test_polish_line_uses_its_mailbox_and_language(cfg):
    sends = []
    make(cfg, FakeSpool(meta(line="pl", did="48320000001", has_audio=False), wav=None), sends).process(Item(ID, 0, False))
    assert sends[0]["to"] == "kontakt@acme.example"
    assert sends[0]["subject"].startswith("[Poczta głosowa] Nieodebrane połączenie")


def test_transcription_chain_exhausted_sends_wav(cfg):
    sends = []
    p = make(cfg, FakeSpool(meta()), sends, transcribe=lambda *a: None)
    assert p.process(Item(ID, 0, True)) == "acked"
    assert "Not transcribed" in sends[0]["subject"]
    assert sends[0]["attachments"][0][0] == f"{ID}.wav"


def test_summary_chain_exhausted_sends_wav_and_transcript(cfg):
    sends = []
    make(cfg, FakeSpool(meta()), sends, transcribe=ok_transcribe, summarise=lambda *a: None).process(Item(ID, 0, True))
    assert "Goedendag" in sends[0]["text"] and sends[0]["attachments"]


def test_send_failure_does_not_ack(cfg):
    sp = FakeSpool(meta(has_audio=False), wav=None)
    assert make(cfg, sp, [], send_error=SendError("down")).process(Item(ID, 0, False)) == "retry"
    assert sp.acked == []
    assert not (cfg.paths.work_dir / ID).exists()


def test_ack_failure_keeps_id_for_ack_only_retry(cfg):
    sends = []
    sp = FakeSpool(meta(has_audio=False), wav=None, ack_error=SpoolError("net"))
    p = make(cfg, sp, sends)
    assert p.process(Item(ID, 0, False)) == "retry"
    assert ID in p.sent_pending_ack
    sp.ack_error = None
    assert p.process(Item(ID, 0, False)) == "acked"
    assert len(sends) == 1 and sp.gets == 1


def test_happy_path_logs_message_with_id_and_providers(cfg):
    logs = []
    make(cfg, FakeSpool(meta()), [], logs=logs, transcribe=ok_transcribe, summarise=ok_summarise).process(Item(ID, 0, True))
    [c] = calls(cfg)
    assert c["outcome"] == "message" and c["message_id"] == "MID"
    assert c["transcribed_by"] == "whisper_local" and c["summarised_by"] == "primary"
    assert c["id"] == ID and c["did"] == "3220000001" and c["caller"] == "+32470123456" and c["language"] == "nl"
    assert f"{ID} be message msg=MID" in logs
    assert not any("+32470123456" in l for l in logs)


def test_missed_call_logged(cfg):
    logs = []
    make(cfg, FakeSpool(meta(has_audio=False), wav=None), [], logs=logs).process(Item(ID, 0, False))
    [c] = calls(cfg)
    assert c["outcome"] == "missed" and c["message_id"] == "MID" and c["has_audio"] is False
    assert c["transcribed_by"] is None and c["summarised_by"] is None
    assert f"{ID} be missed msg=MID" in logs


def test_fallback_outcomes_logged(cfg):
    make(cfg, FakeSpool(meta()), [], transcribe=lambda *a: None).process(Item(ID, 0, True))
    make(cfg, FakeSpool(meta()), [], transcribe=lambda *a: ("Goedendag", "remote"), summarise=lambda *a: None).process(Item(ID, 0, True))
    first, second = calls(cfg)
    assert first["outcome"] == "fallback-transcription" and first["transcribed_by"] is None
    assert second["outcome"] == "fallback-summary" and second["transcribed_by"] == "remote"
    assert second["summarised_by"] is None


def test_send_failure_logs_send_failed_once(cfg):
    logs = []
    sp = FakeSpool(meta(has_audio=False), wav=None)
    p = make(cfg, sp, [], send_error=SendError("down"), logs=logs)
    for _ in range(3):
        p.process(Item(ID, 0, False))
    assert sp.acked == []
    assert [c["outcome"] for c in calls(cfg)] == ["send-failed"]
    assert calls(cfg)[0]["message_id"] is None
    assert logs.count(f"{ID} be send-failed msg=None") == 1


def test_ack_only_retry_logs_acked_after_retry(cfg):
    logs = []
    sp = FakeSpool(meta(has_audio=False), wav=None, ack_error=SpoolError("net"))
    p = make(cfg, sp, [], logs=logs)
    p.process(Item(ID, 0, False))
    sp.ack_error = None
    p.process(Item(ID, 0, False))
    first, second = calls(cfg)
    assert first["outcome"] == "missed"
    assert second["outcome"] == "acked-after-retry" and second["message_id"] == "MID"
    assert second["line"] == "be" and second["caller"] == "+32470123456"
    assert f"{ID} be acked-after-retry msg=MID" in logs


def test_call_log_write_failure_does_not_block_ack(cfg):
    logs = []
    sp = FakeSpool(meta(has_audio=False), wav=None)
    p = make(cfg, sp, [], logs=logs)
    cfg.paths.data_dir.mkdir(parents=True)
    (cfg.paths.data_dir / "calllog").write_text("not a directory")
    assert p.process(Item(ID, 0, False)) == "acked"
    assert sp.acked == [ID] and any("call log" in l for l in logs)


def test_speech_detection_failure_sends_fallback_with_wav(cfg):
    sends = []
    sp = FakeSpool(meta())
    assert make(cfg, sp, sends, speech=BrokenSpeech()).process(Item(ID, 0, True)) == "acked"
    assert sp.acked == [ID] and sends[0]["to"] == "info@acme.example"
    assert "speech detection failed" in sends[0]["text"]
    assert sends[0]["attachments"][0][0] == f"{ID}.wav"
    assert calls(cfg)[0]["outcome"] == "fallback-speech-detection"


def test_unknown_line_retries_then_falls_back_after_five_failures(cfg):
    sends, logs = [], []
    sp = FakeSpool(meta(line="xx"))
    p = make(cfg, sp, sends, logs=logs)
    for _ in range(4):
        assert p.process(Item(ID, 0, True)) == "retry"
    assert sends == [] and sp.acked == []
    assert p.process(Item(ID, 0, True)) == "acked"
    assert sp.acked == [ID]
    fallback, alert = sends
    assert fallback["to"] == "info@acme.example"
    assert "processing failed repeatedly" in fallback["text"]
    assert fallback["attachments"][0][0] == f"{ID}.wav"
    assert alert["to"] == "admin@acme.example" and alert["attachments"] == ()
    assert alert["subject"] == "[Voicemail alert] processing failed"
    assert "+32470123456" not in alert["subject"] + alert["text"]
    assert calls(cfg)[-1]["outcome"] == "fallback-failed-repeatedly"
    assert not any("+32470123456" in l for l in logs)
    assert ID not in p.failures


def test_repeated_failure_uses_known_line_mailbox_without_wav(cfg, monkeypatch):
    sends = []
    p = make(cfg, FakeSpool(meta(line="pl", has_audio=False), wav=None), sends)
    monkeypatch.setattr(processor.render, "missed", lambda *a: (_ for _ in ()).throw(ValueError("bad")))
    for _ in range(5):
        r = p.process(Item(ID, 0, False))
    assert r == "acked"
    assert sends[0]["to"] == "kontakt@acme.example" and sends[0]["attachments"] == ()
    assert sends[1]["to"] == "admin@acme.example"


def test_second_giveup_within_the_hour_does_not_alert_again(cfg):
    sends = []
    p = make(cfg, FakeSpool(meta(line="xx")), sends)
    for item_id in (ID, ID2):
        for _ in range(5):
            p.process(Item(item_id, 0, True))
    assert [s["to"] for s in sends] == ["info@acme.example", "admin@acme.example", "info@acme.example"]


def test_failure_counter_resets_after_success(cfg, monkeypatch):
    sends = []
    p = make(cfg, FakeSpool(meta(has_audio=False), wav=None), sends)
    real = processor.render.missed
    monkeypatch.setattr(processor.render, "missed", lambda *a: (_ for _ in ()).throw(ValueError("bad")))
    for _ in range(4):
        assert p.process(Item(ID, 0, False)) == "retry"
    monkeypatch.setattr(processor.render, "missed", real)
    assert p.process(Item(ID, 0, False)) == "acked"
    assert ID not in p.failures and len(sends) == 1


def test_get_failure_poison_item_falls_back_without_meta(cfg):
    class BadSpool(FakeSpool):
        def get(self, item_id, dest):
            raise ValueError("bad json")

    sends = []
    sp = BadSpool(meta())
    p = make(cfg, sp, sends)
    for _ in range(5):
        r = p.process(Item(ID, 0, True))
    assert r == "acked" and sp.acked == [ID]
    assert sends[0]["to"] == "info@acme.example" and ID in sends[0]["text"]
    assert sends[0]["attachments"] == ()


def test_spool_error_is_not_counted_as_poison(cfg):
    class DownSpool(FakeSpool):
        def get(self, item_id, dest):
            raise SpoolError("net")

    sends = []
    sp = DownSpool(meta())
    p = make(cfg, sp, sends)
    for _ in range(7):
        assert p.process(Item(ID, 0, True)) == "retry"
    assert sends == [] and sp.acked == []


def test_send_retry_reuses_rendered_email_without_rerunning_pipeline(cfg):
    sends, runs = [], []
    sp = FakeSpool(meta())

    def transcribe_once(*a):
        runs.append("t")
        return "Goedendag", "whisper_local"

    p = make(cfg, sp, sends, send_error=SendError("down"), transcribe=transcribe_once, summarise=ok_summarise)
    assert p.process(Item(ID, 0, True)) == "retry"
    assert p.process(Item(ID, 0, True)) == "retry"
    p.d.send = lambda **kw: (sends.append(kw), "MID2")[1]
    assert p.process(Item(ID, 0, True)) == "acked"
    assert runs == ["t"] and sp.gets == 1
    assert sp.acked == [ID] and len(sends) == 1
    assert [c["outcome"] for c in calls(cfg)] == ["send-failed", "message"]
    assert calls(cfg)[-1]["message_id"] == "MID2"
    assert ID not in p.pending_send


def test_send_retry_keeps_wav_attachment(cfg):
    sends = []
    p = make(cfg, FakeSpool(meta(), wav=b"RIFFdata"), sends, send_error=SendError("down"), transcribe=lambda *a: None)
    assert p.process(Item(ID, 0, True)) == "retry"
    p.d.send = lambda **kw: (sends.append(kw), "MID")[1]
    assert p.process(Item(ID, 0, True)) == "acked"
    assert sends[0]["attachments"] == ((f"{ID}.wav", b"RIFFdata", "audio/wav"),)


def test_giveup_without_alerter_still_acks_with_one_fallback_email(cfg):
    sends = []
    p = make(cfg, FakeSpool(meta(line="xx")), sends)
    p.d.alerter = None
    for _ in range(4):
        assert p.process(Item(ID, 0, True)) == "retry"
    assert p.process(Item(ID, 0, True)) == "acked"
    assert len(sends) == 1
    assert sends[0]["to"] == "info@acme.example"
    assert "processing failed repeatedly" in sends[0]["text"]


def test_get_failure_attaches_wav_left_in_workdir_before_the_error(cfg):
    class PartialSpool(FakeSpool):
        def get(self, item_id, dest):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"{item_id}.wav").write_bytes(b"RIFFpartial")
            raise ValueError("bad json")

    sends = []
    p = make(cfg, PartialSpool(meta()), sends)
    for _ in range(5):
        r = p.process(Item(ID, 0, True))
    assert r == "acked"
    assert sends[0]["attachments"] == ((f"{ID}.wav", b"RIFFpartial", "audio/wav"),)


def test_unexpected_send_error_does_not_stick_failures_at_one(cfg):
    """A non-SendError raised from send() (e.g. a bad-encoding/build bug) used to escape the
    pending_send short-circuit branch uncaught, so the failure counter never advanced past 1 and
    the item retried forever without ever reaching the give-up fallback."""
    attempts = []

    def send(**kw):
        attempts.append(kw)
        raise UnicodeEncodeError("ascii", "x", 0, 1, "boom")

    d = Deps(cfg=cfg, spool=FakeSpool(meta()), speech=FakeSpeech(), transcribe=ok_transcribe,
             summarise=ok_summarise, send=send, calllog=CallLog(cfg.paths.data_dir / "calllog", 90))
    d.alerter = Alerter(send=lambda **kw: d.send(**kw), alert_to=cfg.mail.alert_to)
    p = Processor(d)
    for _ in range(10):
        assert p.process(Item(ID, 0, True)) == "retry"
    assert p.failures.get(ID, 0) != 1
    assert len(attempts) >= 6


def test_fallback_send_retry_then_success_alerts_exactly_once(cfg):
    sends = []

    def send(**kw):
        sends.append(kw)
        if len(sends) == 1:
            raise SendError("down")
        return "MID"

    d = Deps(cfg=cfg, spool=FakeSpool(meta(line="xx")), speech=FakeSpeech(), transcribe=unexpected,
             summarise=unexpected, send=send, calllog=CallLog(cfg.paths.data_dir / "calllog", 90))
    d.alerter = Alerter(send=lambda **kw: d.send(**kw), alert_to=cfg.mail.alert_to)
    p = Processor(d)
    for _ in range(4):
        assert p.process(Item(ID, 0, True)) == "retry"
    assert p.process(Item(ID, 0, True)) == "retry"  # give-up triggers; the fallback send itself fails once
    assert p.process(Item(ID, 0, True)) == "acked"  # fallback send retried and succeeds; then the alert
    assert len(sends) == 3
    assert sends[0]["to"] == "info@acme.example" and sends[1]["to"] == "info@acme.example"
    assert sends[2]["to"] == "admin@acme.example"


def test_ack_only_retry_never_resends_the_email(cfg):
    """Once the email has been delivered (item sitting in sent_pending_ack), a persistent
    non-SpoolError ack failure must never trigger give-up's fallback resend - only the ack itself
    is retried, forever."""
    sends = []

    class FlakyAckSpool(FakeSpool):
        def ack(self, item_id):
            raise OSError("disk gone")

    sp = FlakyAckSpool(meta(has_audio=False), wav=None)
    p = make(cfg, sp, sends)
    p.d.alerter = None
    for _ in range(20):
        assert p.process(Item(ID, 0, False)) == "retry"
    assert len(sends) == 1
    assert sends[0]["to"] == "info@acme.example"
    assert sp.acked == []
    assert ID in p.sent_pending_ack


def test_interleaved_unexpected_and_send_errors_still_reach_give_up(cfg):
    """A SendError (handled, returns "retry" without raising) must not reset the failure counter
    that a different, unexpected exception is building up on the same cached pending - otherwise
    the two interleaved never escalate to give-up."""
    attempts = []

    def send(**kw):
        attempts.append(kw)
        n = len(attempts)
        if n <= 9:
            if n % 2 == 1:
                raise UnicodeEncodeError("ascii", "x", 0, 1, "boom")
            raise SendError("down")
        return "MID"

    d = Deps(cfg=cfg, spool=FakeSpool(meta()), speech=FakeSpeech(), transcribe=ok_transcribe,
             summarise=ok_summarise, send=send, calllog=CallLog(cfg.paths.data_dir / "calllog", 90))
    d.alerter = Alerter(send=lambda **kw: d.send(**kw), alert_to=cfg.mail.alert_to)
    p = Processor(d)
    results = [p.process(Item(ID, 0, True)) for _ in range(12)]
    assert "acked" in results
    assert any(c["outcome"] == "fallback-failed-repeatedly" for c in calls(cfg))
    assert ID not in p.failures


def test_send_failed_logged_only_once_per_item_even_after_giveup(cfg):
    attempts = []

    def send(**kw):
        attempts.append(kw)
        n = len(attempts)
        if n == 1:
            raise SendError("down")
        if 2 <= n <= 6:
            raise UnicodeEncodeError("ascii", "x", 0, 1, "boom")
        if n == 7:
            raise SendError("down")
        return "MID"

    d = Deps(cfg=cfg, spool=FakeSpool(meta()), speech=FakeSpeech(), transcribe=ok_transcribe,
             summarise=ok_summarise, send=send, calllog=CallLog(cfg.paths.data_dir / "calllog", 90))
    d.alerter = Alerter(send=lambda **kw: d.send(**kw), alert_to=cfg.mail.alert_to)
    p = Processor(d)
    results = [p.process(Item(ID, 0, True)) for _ in range(9)]
    assert "acked" in results
    outcomes = [c["outcome"] for c in calls(cfg)]
    assert outcomes.count("send-failed") == 1


class OnceFlakyAckSpool(FakeSpool):
    """Delivers normally but fails the very first ack() call with a non-SpoolError, then succeeds."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ack_calls = 0

    def ack(self, item_id):
        self.ack_calls += 1
        if self.ack_calls == 1:
            raise OSError("disk gone")
        self.acked.append(item_id)


def test_send_retries_then_first_ack_failure_does_not_duplicate_the_email(cfg):
    """Regression (repro A): send raises an unexpected error 4x (priming the failure counter close
    to the give-up limit), the 5th send succeeds, and then - on that same successful delivery - the
    very first ack() call raises a non-SpoolError. That must never be misread as a fifth send/pipeline
    failure and must never trigger give-up's duplicate fallback email."""
    sends = []
    send_attempts = []

    def send(**kw):
        send_attempts.append(1)
        if len(send_attempts) <= 4:
            raise UnicodeEncodeError("ascii", "x", 0, 1, "boom")
        sends.append(kw)
        return "MID"

    sp = OnceFlakyAckSpool(meta())
    d = Deps(cfg=cfg, spool=sp, speech=FakeSpeech(), transcribe=ok_transcribe, summarise=ok_summarise,
             send=send, calllog=CallLog(cfg.paths.data_dir / "calllog", 90))
    d.alerter = Alerter(send=lambda **kw: d.send(**kw), alert_to=cfg.mail.alert_to)
    p = Processor(d)
    for _ in range(4):
        assert p.process(Item(ID, 0, True)) == "retry"
    assert p.process(Item(ID, 0, True)) == "retry"   # 5th send succeeds; the first ack then fails
    assert p.process(Item(ID, 0, True)) == "acked"   # ack retried and succeeds
    assert len(sends) == 1
    assert sends[0]["to"] == "info@acme.example"
    assert sp.acked == [ID] and sp.ack_calls == 2


def test_transcribe_retries_then_first_ack_failure_does_not_duplicate_the_email(cfg):
    """Regression (repro B): the same scenario as above, but the pre-delivery failures come from
    the pipeline (transcribe) rather than from send() itself, so delivery happens via process()'s
    main path rather than the pending_send short-circuit."""
    sends = []
    transcribe_attempts = []

    def transcribe(wav, lang, candidates):
        transcribe_attempts.append(1)
        if len(transcribe_attempts) <= 4:
            raise RuntimeError("stt exploded")
        return "Goedendag", "whisper_local"

    def send(**kw):
        sends.append(kw)
        return "MID"

    sp = OnceFlakyAckSpool(meta())
    d = Deps(cfg=cfg, spool=sp, speech=FakeSpeech(), transcribe=transcribe, summarise=ok_summarise,
             send=send, calllog=CallLog(cfg.paths.data_dir / "calllog", 90))
    d.alerter = Alerter(send=lambda **kw: d.send(**kw), alert_to=cfg.mail.alert_to)
    p = Processor(d)
    for _ in range(4):
        assert p.process(Item(ID, 0, True)) == "retry"
    assert p.process(Item(ID, 0, True)) == "retry"   # pipeline+send succeed; the first ack then fails
    assert p.process(Item(ID, 0, True)) == "acked"   # ack retried and succeeds
    assert len(sends) == 1
    assert sends[0]["to"] == "info@acme.example"
    assert sp.acked == [ID] and sp.ack_calls == 2
