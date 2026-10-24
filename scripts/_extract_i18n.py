from html.parser import HTMLParser
from pathlib import Path
import re

AR = re.compile(r"[\u0600-\u06FF]")
QUOTED = re.compile(r'''(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')''', re.S)


def strip_exprs(text):
    out = []
    i = 0
    n = 0
    while i < len(text):
        if text.startswith("${", i):
            depth = 1
            j = i + 2
            while j < len(text) and depth:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            out.append("{" + str(n) + "}")
            n += 1
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


class FragCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.texts = []
        self.attrs = []

    def _attr(self, attrs):
        for key, value in attrs:
            if key in {"placeholder", "title", "aria-label", "alt", "value"} and value and AR.search(value):
                self.attrs.append(value)

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        self._attr(attrs)

    def handle_startendtag(self, tag, attrs):
        self._attr(attrs)

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if self.skip:
            return
        value = re.sub(r"\s+", " ", data).strip()
        if value and AR.search(value):
            self.texts.append(value)


def main():
    out = set()
    for path in sorted(Path("attendance_app/static/js").glob("*.js")):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"`((?:[^`\\]|\\.|\\`)*)`", source, re.S):
            raw = match.group(1)
            if not AR.search(raw):
                continue
            value = strip_exprs(raw)
            if "<" in value and ">" in value:
                collector = FragCollector()
                try:
                    collector.feed(value)
                    collector.close()
                except Exception:
                    pass
                out.update(collector.texts)
                out.update(collector.attrs)
            else:
                value = re.sub(r"\s+", " ", value).strip()
                if value:
                    out.add(value)
        for match in QUOTED.finditer(source):
            raw = match.group(1) or match.group(2) or ""
            if not AR.search(raw):
                continue
            raw = raw.replace('\\"', '"').replace("\\'", "'").replace("\\n", " ")
            raw = re.sub(r"\s+", " ", raw).strip()
            if raw:
                out.add(raw)
    print("js ui candidates", len(out))
    Path("scripts/_i18n_js_text.txt").write_text("\n".join(sorted(out)) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
