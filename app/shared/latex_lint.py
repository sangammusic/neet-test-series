"""
Dependency-free LaTeX sanity checks for question text / options / explanation.

WHY: MathJax shows red "Double exponent: use braces to clarify" / "Missing close brace" when the
stored LaTeX is malformed (e.g. x^2^3, 10^-4^{-1}). That is a DATA problem, so it is caught at upload /
edit time and reported per field. Only text inside math delimiters (\\( \\), \\[ \\], $ $, $$ $$) is checked.

Rule mirrored from MathJax: within ONE brace level, a base may take at most one ^ and one _.
`x^2^3` (second ^ on the same base) is an error; `x^{a^b}`, `x^2 y^3`, `x_1^2` are fine.
"""
import re

_MATH = re.compile(r"\\\((.+?)\\\)|\\\[(.+?)\\\]|\$\$(.+?)\$\$|\$([^$\n]+?)\$", re.S)
_CMD = re.compile(r"\\[a-zA-Z]+|\\.")


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
