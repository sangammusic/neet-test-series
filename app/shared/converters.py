"""
URL converter for folder / quiz names that may contain characters illegal inside ONE path segment.

ROOT CAUSE of the "error page on Analyse / result buttons" bug: folder and quiz names are free
text (the admin default folder is literally "General / Uncategorized"). Flask's <string> converter
rejects "/", so every practice page after the quiz list returned 404. Here "/" (and a few other
troublesome characters) become reversible tokens; plain names are untouched, so old links still work.
"""
from urllib.parse import quote
from werkzeug.routing import BaseConverter

_ENC = (("~", "~t"), ("/", "~s"), ("\\", "~b"), ("?", "~q"), ("#", "~h"), ("%", "~p"))
_REV = {tok: ch for ch, tok in _ENC}


def encode_name(name):
    out = str(name)
    for ch, tok in _ENC:
        out = out.replace(ch, tok)
    return out


def decode_name(token):
    out, i = [], 0
    while i < len(token):
        if token[i] == "~" and token[i:i + 2] in _REV:
            out.append(_REV[token[i:i + 2]]); i += 2
        else:
            out.append(token[i]); i += 1
    return "".join(out)


class NameConverter(BaseConverter):
    regex = r"[^/]+"

    def to_python(self, value):
        return decode_name(value)

    def to_url(self, value):
        return quote(encode_name(value), safe="~")
