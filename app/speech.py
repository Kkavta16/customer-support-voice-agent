"""Text clean-up applied right before text-to-speech."""
import re

_DIGIT = r"(?:\d+|zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_TRAILING_DIGITS = re.compile(rf"(?:{_DIGIT}[\s,.\-]*)+$", re.I)
_LEADS_INTO_NUMBER = re.compile(
    r"\b(ends? (in|with)|ending (in|with)|number is|digits are|order is|it'?s|is)[\s.,…]*$", re.I
)
_NUMBER_CONTEXT = re.compile(r"\b(phone|number|digits?|ends?|ending|order)\b", re.I)
_DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
                "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def looks_like_unfinished_number(text: str) -> bool:
    """True when a caller seems to be partway through reading out a number, e.g.
    "my phone number ends in", "it ends in 4 8", or just "4 8". Used to keep their turn open
    through the pauses people leave between digits."""
    text = text.strip()
    if not text:
        return False
    if _LEADS_INTO_NUMBER.search(text):
        return True
    tail = _TRAILING_DIGITS.search(text)
    if not tail:
        return False
    digits = re.sub(r"\D", "", re.sub(r"[a-z]+", lambda m: _DIGIT_WORDS.get(m.group().lower(), ""),
                                       tail.group(), flags=re.I))
    only_digits = tail.start() == 0
    return len(digits) < 4 and (only_digits or bool(_NUMBER_CONTEXT.search(text)))


_SPACED_DIGITS = re.compile(r"\b\d(?:[\s\-]+\d)+\b")


def join_spoken_digits(text: str) -> str:
    """'It ends in   4   8   2   1' -> 'It ends in 4821'.
    Digits read out one at a time are transcribed separately and joined with spaces. Given the
    spaced form, the LLM read the digits back instead of looking up the order; joined, it calls
    the tool right away."""
    text = re.sub(r"\s+", " ", text).strip()
    return _SPACED_DIGITS.sub(lambda m: re.sub(r"[\s\-]", "", m.group()), text)


_SKU = re.compile(r"\[?\b[A-Z]{2}-\d{3}\b\]?:?\s*")
_BULLET = re.compile(r"^\s*[-•]\s+", re.M)
_RUPEES = re.compile(r"(?:₹|\bRs\.?|\bINR)\s?([\d,]+(?:\.\d+)?)")


def clean_for_speech(text: str) -> str:
    """Small models sometimes emit markdown, product codes or currency symbols even when told not
    to. Strip what a listener should never hear."""
    text = _SKU.sub("", text)
    text = _BULLET.sub("", text)
    text = _RUPEES.sub(r"\1 rupees", text)
    text = re.sub(r"[*_#`]", "", text)
    return re.sub(r"\s+", " ", text).strip()
