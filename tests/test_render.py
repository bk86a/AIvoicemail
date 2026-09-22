import pytest

from aivoicemail import render
from aivoicemail.config import Line

BE = Line("be", "3220000001", "info@acme.example", "en", ("nl", "fr", "en"), "auto", "acme.example/privacy")
META = {"id": "0f8fad5b-d9cb-469f-a165-70867728950e", "line": "be", "did": "3220000001", "caller": "+32470123456",
        "lang_choice": "nl", "started_at": "2026-09-13T09:00:00Z", "duration_s": 30}
SUMMARY = {"caller_name": "Jan", "company": None, "subject": "Offerte", "callback_number": None, "language": "nl",
           "urgency": "normal", "summary": "Vraagt een offerte.", "requested_action": "Terugbellen"}
PROVIDERS = {"transcript": "whisper_local", "summary": "primary"}


def line(lang):
    return Line("be", "3220000001", "info@acme.example", lang, ("nl",), "nl", "acme.example/privacy")


def test_label_sets_complete_and_without_em_dash():
    keys = set(render.LABELS["en"])
    assert set(render.LABELS) == {"en", "nl", "fr", "de", "pl"}
    for lang, labels in render.LABELS.items():
        assert set(labels) == keys, lang
        assert set(labels["urgency_values"]) == {"low", "normal", "high"}, lang
        assert "—" not in repr(labels), lang
        assert "{t}" in labels["providers"] and "{s}" in labels["providers"], lang
        assert "{reason}" in labels["fallback_text"], lang


def test_message_english():
    e = render.message(BE, META, "Goedendag, graag een offerte.", SUMMARY, PROVIDERS)
    assert e.subject == "[Voicemail] normal - Jan - Offerte"
    for part in ("Caller: Jan", "Company: -", "Callback number: +32470123456", "Transcript (nl):",
                 "Goedendag, graag een offerte.", "  Line: BE (3220000001)", f"  ID: {META['id']}",
                 "Transcribed by: whisper_local; summarised by: primary"):
        assert part in e.text, part


@pytest.mark.parametrize("lang,tag,caller", [("nl", "[Voicemail]", "Beller"), ("fr", "[Messagerie vocale]", "Appelant"),
                                             ("de", "[Mailbox]", "Anrufer"), ("pl", "[Poczta głosowa]", "Dzwoniący")])
def test_message_other_languages(lang, tag, caller):
    e = render.message(line(lang), META, "t", SUMMARY, PROVIDERS)
    assert e.subject.startswith(tag + " ") and f"{caller}: Jan" in e.text


def test_unknown_email_language_falls_back_to_english():
    assert render.missed(line("it"), META).subject.startswith("[Voicemail] Missed call")


def test_missed_and_fallback():
    assert render.missed(BE, META).subject == "[Voicemail] Missed call - +32470123456"
    f = render.fallback(BE, META, "partial text", "summary failed")
    assert f.subject == "[Voicemail] Not transcribed - +32470123456"
    assert "(summary failed)" in f.text and "partial text" in f.text
    assert "Transcript:" not in render.fallback(BE, META, None, "transcription failed").text


def test_subject_is_single_line_and_bounded():
    s = dict(SUMMARY, caller_name="Evil\r\nBcc: x@acme.example", subject="A" * 500)
    e = render.message(BE, META, "t", s, PROVIDERS)
    assert "\n" not in e.subject and "\r" not in e.subject and len(e.subject) <= 160


def test_alert():
    a = render.alert("stale", "2 item(s) waiting")
    assert a.subject == "[Voicemail alert] stale" and a.text == "2 item(s) waiting\n"
