"""Swear words and slurs, hidden on the clip when the person asks for it.

The word stays where it was said, so the captions keep their timing: only its letters after the first are
starred out ("fucking" becomes "f******"). The hook, title and post text get the same treatment.

The list is short on purpose. It is what gets a short limited or taken down, not every rude word there is."""
import re

# Each pattern matches the whole of a word. Slurs are matched in their common spellings.
_PATTERNS = [
    r"f+u+c+k+\w*", r"motherf\w*", r"\w*fucker\w*", r"bullshit\w*", r"bitch\w*",
    r"sh+i+t+(?:s|ty|tier|tiest|ting|ted|head\w*|hole\w*|bag\w*|face\w*|show\w*)?",
    r"cunt\w*", r"dick(?:head)?s?", r"cock(?:sucker)?s?", r"puss(?:y|ies)", r"ass(?:hole\w*|es)?", r"arse(?:hole)?s?",
    r"bastards?", r"wh+o+r+e+s?", r"slut\w*", r"twats?", r"wank(?:er|ers|ing)?", r"pricks?",
    r"n+[i1!]+gg?(?:a+h*|e+r+s?|u+h+|ahs?|as|az|ers)s?", r"f+a+g+(?:g+o+t+)?s?",
    r"retard(?:ed|s)?", r"chinks?", r"spics?", r"kikes?", r"trann(?:y|ies)",
]
_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(_PATTERNS) + r")(?![A-Za-z])", re.I)


def _star(m: re.Match) -> str:
    w = m.group(0)
    return w[0] + "*" * (len(w) - 1)


def text(s: str) -> str:
    """The same text with every listed word starred out."""
    return _RE.sub(_star, s or "")


def words(ws: list) -> list:
    """Caption words with the listed ones starred out. Timings are left exactly as they were."""
    return [dict(w, w=text(w.get("w", ""))) for w in ws]


def has_any(s: str) -> bool:
    return bool(_RE.search(s or ""))
