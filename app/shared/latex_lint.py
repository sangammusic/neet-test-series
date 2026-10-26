# STUDY SANGAM HUB v5.0 — LaTeX Sanity Checks, Citation Artifact Sanitizer & Auto-Math Normalization
"""
Dependency-free LaTeX sanity checks and text normalization for question_text,
option_a..option_d, and explanation.

Fixes:
1. Strips AI citation/extraction span tags such as `[span_0](start_span)`,
   `[span_0](end_span)`, `[cite_start]`, `[cite: 1, 2]` that polluted imported
   NEET questions (seen on the live screenshot).
2. Normalizes double-escaped LaTeX delimiters (`\\\\(` -> `\\(`, `\\\\[` -> `\\[`)
   and common raw LaTeX commands (`\\frac`, `\\sqrt`, `\\times`, Greek letters,
   `\\rightarrow`, etc.) when they appear outside math delimiters so MathJax
   always renders clean mathematical and chemical expressions instead of showing
   raw backslashes.
3. Validates brace balance and double-exponent / double-subscript rules.
"""
import re

_MATH = re.compile(r"\\\((.+?)\\\)|\\\[(.+?)\\\]|\$\$(.+?)\$\$|\$([^$\n]+?)\$", re.S)
_CMD = re.compile(r"\\[a-zA-Z]+|\\.")

# Matches AI citation / span artifacts like [span_0](start_span), [span_12](end_span), [cite_start], [cite: 12]
_SPAN_ARTIFACT_RE = re.compile(
    r"\[span_\d+\]\((?:start_span|end_span)\)"
    r"|\[cite_start\]"
    r"|\[cite_end\]"
    r"|\[cite:\s*[\d,\s\-]+\]",
    re.IGNORECASE,
)

# Common standalone LaTeX commands that should be wrapped in \( ... \) if left outside math mode
_BARE_LATEX_EXPR_RE = re.compile(
    r"(?<!\\)\\(?:"
    r"frac\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
    r"|sqrt(?:\[[^\]]*\])?\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
    r"|overrightarrow\s*\{[^{}]+\}"
    r"|vec\s*\{[^{}]+\}"
    r"|hat\s*\{[^{}]+\}"
    r"|bar\s*\{[^{}]+\}"
    r"|text\s*\{[^{}]+\}"
    r"|mathrm\s*\{[^{}]+\}"
    r"|alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|varphi|chi|psi|omega"
    r"|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega"
    r"|times|div|pm|mp|cdot|leq|geq|neq|approx|equiv|propto|infty|partial|nabla|int|sum|prod"
    r"|rightarrow|leftarrow|leftrightarrow|Rightarrow|Leftarrow|Leftrightarrow|rightleftharpoons|uparrow|downarrow"
    r"|circ|degree|angle|triangle|perp|parallel"
    r")(?:\s*[\^_]\s*(?:\{[^{}]+\}|[A-Za-z0-9+\-]+))*",
)


def clean_question_text(text):
    """
    Cleans and normalizes a question string (stem, option, or explanation):
      1. Strips `[span_0](start_span)` / `[span_0](end_span)` / `[cite: ...]` artifacts.
      2. Unescapes accidentally double-escaped LaTeX delimiters (`\\\\(` -> `\\(`, `\\\\frac` -> `\\frac`).
      3. Wraps bare LaTeX commands outside math delimiters into `\\( ... \\)` so MathJax renders them.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return ""

    # 1. Strip AI citation/span markers
    s = _SPAN_ARTIFACT_RE.sub("", text)

    # 2. Fix double-escaped backslashes before math delimiters or known LaTeX commands
    s = re.sub(r"\\\\([()\[\]])", r"\\\1", s)
    s = re.sub(
        r"\\\\(frac|sqrt|times|div|pm|cdot|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|omega|Delta|Omega|rightarrow|leftrightharpoons|rightleftharpoons|leq|geq|neq|approx|infty|int|sum|vec|hat|mathrm|text)\b",
        r"\\\1",
        s,
    )

    # 3. If string has bare LaTeX commands outside existing math delimiters, wrap only the non-math segments
    if "\\" in s:
        parts = []
        last_idx = 0
        for m in _MATH.finditer(s):
            outside = s[last_idx:m.start()]
            if "\\" in outside:
                outside = _BARE_LATEX_EXPR_RE.sub(lambda match: f"\\({match.group(0)}\\)", outside)
            parts.append(outside)
            parts.append(m.group(0))
            last_idx = m.end()
        tail = s[last_idx:]
        if "\\" in tail:
            tail = _BARE_LATEX_EXPR_RE.sub(lambda match: f"\\({match.group(0)}\\)", tail)
        parts.append(tail)
        s = "".join(parts)

    # Collapse any multiple spaces left behind by stripped span tags
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    return s


def clean_question_dict(q: dict | None) -> dict | None:
    """Clean all text fields of a question row dictionary and normalize image_url."""
    if not q or not isinstance(q, dict):
        return q
    out = dict(q)
    for k in ("question_text", "option_a", "option_b", "option_c", "option_d", "explanation", "topic"):
        if k in out and out[k] is not None:
            out[k] = clean_question_text(out[k])
    raw_img = str(out.get("image_url") or "").strip()
    if re.match(r"^https?://\S+$", raw_img, re.IGNORECASE) and raw_img.lower() not in ("https://null", "http://null", "https://none", "http://none"):
        out["image_url"] = raw_img
        out["has_image"] = True
    else:
        out["image_url"] = None
        out["has_image"] = False
    return out

def _strip_escaped(t):
    return t.replace("\\{", "").replace("\\}", "").replace("\\^", "").replace("\\_", "")


def _group_end(t, i):
    """t[i] == '{'. Index of the matching '}'."""
    d = 0
    for k in range(i, len(t)):
        if t[k] == "{":
            d += 1
        elif t[k] == "}":
            d -= 1
            if d == 0:
                return k
    return len(t) - 1


def _scan(u, problems):
    i, n = 0, len(u)
    seen_sup = seen_sub = False     # scripts already attached to the CURRENT base
    want_arg = False                # previous token was ^ or _ -> next token is its argument
    while i < n:
        ch = u[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "{":
            end = _group_end(u, i)
            _scan(u[i + 1:end], problems)
            i, is_script = end + 1, False
        elif ch == "\\":
            m = _CMD.match(u, i)
            i, is_script = (m.end() if m else i + 1), False
        else:
            i, is_script = i + 1, ch in "^_"
        if want_arg:
            want_arg = False
            continue
        if is_script:
            if ch == "^":
                if seen_sup:
                    problems.append("double exponent (wrap in braces, e.g. {x^{a}}^{b})")
                seen_sup = True
            else:
                if seen_sub:
                    problems.append("double subscript (wrap in braces, e.g. {x_{a}}_{b})")
                seen_sub = True
            want_arg = True
        else:
            seen_sup = seen_sub = False     # a new base starts


def lint_math(segment):
    t = _strip_escaped(segment)
    if t.count("{") != t.count("}"):
        return ["unbalanced braces { }"]
    problems = []
    _scan(t, problems)
    return list(dict.fromkeys(problems))


def lint_text(text):
    if not text or not isinstance(text, str):
        return []
    found = []
    for m in _MATH.finditer(text):
        found.extend(lint_math(next(g for g in m.groups() if g is not None)))
    if text.count("\\(") != text.count("\\)"):
        found.append("unbalanced \\( \\) math delimiters")
    if text.count("\\[") != text.count("\\]"):
        found.append("unbalanced \\[ \\] math delimiters")
    return list(dict.fromkeys(found))


def lint_question_payload(payload):
    """{field: [problems]} for question_text, options A-D and explanation."""
    out = {}
    for f in ("question_text", "option_a", "option_b", "option_c", "option_d", "explanation"):
        p = lint_text(payload.get(f))
        if p:
            out[f] = p
    return out
