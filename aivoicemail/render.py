"""Plain-text notification emails in the line's email_language (transcript stays in the caller's language)."""
from dataclasses import dataclass

LABELS = {
    "en": {
        "tag": "[Voicemail]", "missed": "Missed call", "not_transcribed": "Not transcribed",
        "caller": "Caller", "company": "Company", "callback": "Callback number", "urgency": "Urgency",
        "subject": "Subject", "summary": "Summary", "action": "Requested action", "transcript": "Transcript",
        "call": "Call", "number": "Caller ID", "line": "Line", "start": "Started", "duration": "Duration",
        "providers": "Transcribed by: {t}; summarised by: {s}",
        "urgency_values": {"low": "low", "normal": "normal", "high": "high"},
        "missed_text": "A call was received but no message was left.",
        "fallback_text": "The message could not be processed automatically ({reason}). The recording is attached.",
        "unknown": "unknown",
    },
    "nl": {
        "tag": "[Voicemail]", "missed": "Gemiste oproep", "not_transcribed": "Niet uitgeschreven",
        "caller": "Beller", "company": "Bedrijf", "callback": "Terugbelnummer", "urgency": "Urgentie",
        "subject": "Onderwerp", "summary": "Samenvatting", "action": "Gevraagde actie", "transcript": "Transcriptie",
        "call": "Oproep", "number": "Nummer beller", "line": "Lijn", "start": "Begin", "duration": "Duur",
        "providers": "Uitgeschreven door: {t}; samengevat door: {s}",
        "urgency_values": {"low": "laag", "normal": "normaal", "high": "hoog"},
        "missed_text": "Er kwam een oproep binnen, maar er werd geen bericht ingesproken.",
        "fallback_text": "Het bericht kon niet automatisch verwerkt worden ({reason}). De opname zit in de bijlage.",
        "unknown": "onbekend",
    },
    "fr": {
        "tag": "[Messagerie vocale]", "missed": "Appel manqué", "not_transcribed": "Non transcrit",
        "caller": "Appelant", "company": "Société", "callback": "Numéro de rappel", "urgency": "Urgence",
        "subject": "Objet", "summary": "Résumé", "action": "Action demandée", "transcript": "Transcription",
        "call": "Appel", "number": "Numéro de l'appelant", "line": "Ligne", "start": "Début", "duration": "Durée",
        "providers": "Transcrit par : {t} ; résumé par : {s}",
        "urgency_values": {"low": "faible", "normal": "normale", "high": "élevée"},
        "missed_text": "Un appel a été reçu, mais aucun message n'a été laissé.",
        "fallback_text": "Le message n'a pas pu être traité automatiquement ({reason}). L'enregistrement est joint.",
        "unknown": "inconnu",
    },
    "de": {
        "tag": "[Mailbox]", "missed": "Verpasster Anruf", "not_transcribed": "Nicht transkribiert",
        "caller": "Anrufer", "company": "Firma", "callback": "Rückrufnummer", "urgency": "Dringlichkeit",
        "subject": "Betreff", "summary": "Zusammenfassung", "action": "Gewünschte Aktion", "transcript": "Transkript",
        "call": "Anruf", "number": "Rufnummer", "line": "Leitung", "start": "Beginn", "duration": "Dauer",
        "providers": "Transkribiert von: {t}; zusammengefasst von: {s}",
        "urgency_values": {"low": "niedrig", "normal": "normal", "high": "hoch"},
        "missed_text": "Ein Anruf ist eingegangen, aber es wurde keine Nachricht hinterlassen.",
        "fallback_text": "Die Nachricht konnte nicht automatisch verarbeitet werden ({reason}). Die Aufnahme ist angehängt.",
        "unknown": "unbekannt",
    },
    "pl": {
        "tag": "[Poczta głosowa]", "missed": "Nieodebrane połączenie", "not_transcribed": "Bez transkrypcji",
        "caller": "Dzwoniący", "company": "Firma", "callback": "Numer do oddzwonienia", "urgency": "Pilność",
        "subject": "Temat", "summary": "Streszczenie", "action": "Oczekiwane działanie", "transcript": "Transkrypcja",
        "call": "Połączenie", "number": "Numer dzwoniącego", "line": "Linia", "start": "Początek", "duration": "Czas trwania",
        "providers": "Transkrypcja: {t}; streszczenie: {s}",
        "urgency_values": {"low": "niska", "normal": "normalna", "high": "wysoka"},
        "missed_text": "Odebrano połączenie, ale nie nagrano wiadomości.",
        "fallback_text": "Nie udało się automatycznie przetworzyć wiadomości ({reason}). Nagranie w załączniku.",
        "unknown": "nieznany",
    },
}
MAX_SUBJECT = 160


@dataclass(frozen=True)
class Email:
    subject: str
    text: str


def _labels(line):
    return LABELS.get(line.email_language, LABELS["en"])


def _clean(value, limit=MAX_SUBJECT):
    return " ".join(str(value).split())[:limit]


def _subject(parts):
    return _clean(" - ".join(p for p in parts if p))


def _call_block(L, meta):
    return "\n".join([
        f"{L['call']}:",
        f"  {L['number']}: {meta.get('caller')}",
        f"  {L['line']}: {str(meta.get('line')).upper()} ({meta.get('did')})",
        f"  {L['start']}: {meta.get('started_at')}",
        f"  {L['duration']}: {meta.get('duration_s')} s",
        f"  ID: {meta.get('id')}",
    ])


def message(line, meta, transcript, summary, providers):
    L = _labels(line)
    who = summary.get("caller_name") or meta["caller"]
    urgency = L["urgency_values"].get(summary.get("urgency"), summary.get("urgency"))
    subject = f"{L['tag']} " + _subject([urgency, _clean(who, 60), _clean(summary.get("subject") or "", 80)])
    body = "\n".join([
        f"{L['caller']}: {summary.get('caller_name') or L['unknown']}",
        f"{L['company']}: {summary.get('company') or '-'}",
        f"{L['callback']}: {summary.get('callback_number') or meta['caller']}",
        f"{L['urgency']}: {urgency}",
        f"{L['subject']}: {summary.get('subject') or '-'}",
        "",
        f"{L['summary']}:",
        summary.get("summary") or "-",
        "",
        f"{L['action']}: {summary.get('requested_action') or '-'}",
        "",
        f"{L['transcript']} ({summary.get('language') or '?'}):",
        transcript,
        "",
        _call_block(L, meta),
        "",
        L["providers"].format(t=providers.get("transcript"), s=providers.get("summary")),
    ])
    return Email(_clean(subject), body)


def missed(line, meta):
    L = _labels(line)
    return Email(_clean(f"{L['tag']} {L['missed']} - {meta.get('caller')}"),
                 f"{L['missed_text']}\n\n{_call_block(L, meta)}\n")


def fallback(line, meta, transcript, reason):
    L = _labels(line)
    parts = [L["fallback_text"].format(reason=reason), "", _call_block(L, meta)]
    if transcript:
        parts += ["", f"{L['transcript']}:", transcript]
    return Email(_clean(f"{L['tag']} {L['not_transcribed']} - {meta.get('caller')}"), "\n".join(parts) + "\n")


def alert(kind, detail):
    return Email(_clean(f"[Voicemail alert] {kind}"), f"{detail}\n")
