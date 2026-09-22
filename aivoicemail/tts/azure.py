"""Azure AI Speech TTS (optional): IPA phoneme overrides, exact 0 ms leading/trailing silence,
per-segment pauses (break_ms, default 400) and per-prompt sentence pauses (sentence_ms)."""
import re
from xml.sax.saxutils import escape, quoteattr

from .. import http


class AzureEngine:
    def __init__(self, region, key, voices, pronunciation=None, *, post=None):
        self.url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        self.key, self.voices, self.pronunciation = key, dict(voices), pronunciation or {}
        self._post = post or http.post_bytes

    def say(self, seg) -> str:
        text = escape(seg.text)
        for word, per_lang in self.pronunciation.items():
            ipa = per_lang.get(seg.lang)
            if ipa:
                tag = f'<phoneme alphabet="ipa" ph={quoteattr(ipa)}>{escape(word)}</phoneme>'
                text = re.sub(rf"\b{re.escape(escape(word))}\b", lambda m: tag, text)
        return text

    def ssml(self, prompt) -> str:
        sentence = (f'<mstts:silence type="Sentenceboundary-exact" value="{prompt.sentence_ms}ms"/>'
                    if prompt.sentence_ms is not None else "")
        voices = "".join(
            f'<voice name={quoteattr(self.voices[s.lang])}>'
            '<mstts:silence type="Leading-exact" value="0ms"/>'
            '<mstts:silence type="Tailing-exact" value="0ms"/>'
            f'{sentence}'
            f'<lang xml:lang="{self.voices[s.lang][:5]}">{self.say(s)}</lang>'
            f'<break time="{400 if s.break_ms is None else s.break_ms}ms"/></voice>'
            for s in prompt.segments)
        lang = self.voices[prompt.segments[0].lang][:5]
        return (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
                f'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="{lang}">{voices}</speak>')

    def render(self, prompt) -> bytes:
        _, body = self._post(self.url, self.ssml(prompt).encode(), headers={
            "Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "riff-8khz-16bit-mono-pcm", "User-Agent": "aivoicemail"})
        if body[:4] != b"RIFF":
            raise RuntimeError("Azure returned no WAV")
        return body
