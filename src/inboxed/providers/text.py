import re
import unicodedata
from html.parser import HTMLParser


class TextHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "br", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_text(value: str) -> str:
    parser = TextHTMLParser()
    parser.feed(value)
    return re.sub(r"[ \t]+", " ", "".join(parser.parts)).strip()


def compact_display_text(value: str) -> str:
    """Tidy layout whitespace for reading without rewriting stored message text."""
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    value = value.replace("\u200b", "").replace("\u2060", "").replace("\ufeff", "")
    lines = []
    for line in value.split("\n"):
        line = re.sub(r"[^\S\n]+", " ", line).strip()
        # HTML emails often nest blocks, producing runs of blank lines.
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip()


def decode_mail_text(raw: bytes, charset: str | None = None) -> str:
    """Decode MIME text, recovering UTF-8 bytes mislabeled as a legacy charset."""
    charset = charset or "utf-8"
    try:
        decoded = raw.decode(charset)
    except (LookupError, UnicodeDecodeError):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return raw.decode(charset, errors="replace")
            except LookupError:
                return raw.decode("utf-8", errors="replace")
    # Some senders/API payloads retain an old charset after conversion to UTF-8.
    # Require both valid UTF-8 bytes and a characteristic mojibake sequence;
    # preserve properly declared legacy text when either check fails.
    if re.search(r"[ÃÂĂÄÅ][\u0080-\u00bf\u0100-\u017f\u02c0-\u02ff]", decoded):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return decoded
