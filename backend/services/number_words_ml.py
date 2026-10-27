"""Malayalam number words (cardinals, decimals, percentages).

``num2words`` has no Malayalam (``ml``) locale, so without this module
Malayalam text would reach the TTS engine with raw digits. The rendering here
is pure Python with no dependencies and is deliberately narrow, matching the
conservative scope of :mod:`services.text_normalization`: integers up to six
digits (Indian grouping with ``laksham``; crore-scale is out), decimals read
digit by digit, and percentages.

Output is Malayalam script only, words separated by single ASCII spaces, in
modern atomic-chillu spelling, with no digits and no joiner characters.
"""
from __future__ import annotations

_ZERO = "പൂജ്യം"
_UNITS = ["", "ഒന്ന്", "രണ്ട്", "മൂന്ന്", "നാല്", "അഞ്ച്", "ആറ്", "ഏഴ്", "എട്ട്", "ഒൻപത്"]
_TEENS = [
    "പത്ത്", "പതിനൊന്ന്", "പന്ത്രണ്ട്", "പതിമൂന്ന്", "പതിനാല്",
    "പതിനഞ്ച്", "പതിനാറ്", "പതിനേഴ്", "പതിനെട്ട്", "പത്തൊൻപത്",
]
# Indexed by the tens digit (2..9); entries 0 and 1 are unused.
_TENS = ["", "", "ഇരുപത്", "മുപ്പത്", "നാൽപത്", "അൻപത്", "അറുപത്", "എഴുപത്", "എൺപത്", "തൊണ്ണൂറ്"]
_TENS_PREFIX = ["", "", "ഇരുപത്തി", "മുപ്പത്തി", "നാൽപത്തി", "അൻപത്തി", "അറുപത്തി", "എഴുപത്തി", "എൺപത്തി", "തൊണ്ണൂറ്റി"]
# Indexed by the hundreds digit (1..9).
_HUNDREDS = ["", "നൂറ്", "ഇരുനൂറ്", "മുന്നൂറ്", "നാനൂറ്", "അഞ്ഞൂറ്", "അറുനൂറ്", "എഴുനൂറ്", "എണ്ണൂറ്", "തൊള്ളായിരം"]
_HUNDREDS_PREFIX = [
    "", "നൂറ്റി", "ഇരുനൂറ്റി", "മുന്നൂറ്റി", "നാനൂറ്റി", "അഞ്ഞൂറ്റി",
    "അറുനൂറ്റി", "എഴുനൂറ്റി", "എണ്ണൂറ്റി", "തൊള്ളായിരത്തി",
]
# "N thousand" for N = 1..19 (irregular, so tabulated; index 0 unused).
_THOUSANDS_SMALL = [
    "", "ആയിരം", "രണ്ടായിരം", "മൂവായിരം", "നാലായിരം", "അയ്യായിരം", "ആറായിരം",
    "ഏഴായിരം", "എണ്ണായിരം", "ഒൻപതിനായിരം", "പതിനായിരം", "പതിനൊന്നായിരം",
    "പന്ത്രണ്ടായിരം", "പതിമൂന്നായിരം", "പതിനാലായിരം", "പതിനയ്യായിരം",
    "പതിനാറായിരം", "പതിനേഴായിരം", "പതിനെട്ടായിരം", "പത്തൊൻപതിനായിരം",
]
# Whole tens of thousands, indexed by the tens digit (2..9).
_THOUSANDS_TENS = [
    "", "", "ഇരുപതിനായിരം", "മുപ്പതിനായിരം", "നാൽപതിനായിരം", "അൻപതിനായിരം",
    "അറുപതിനായിരം", "എഴുപതിനായിരം", "എൺപതിനായിരം", "തൊണ്ണൂറായിരം",
]
# "unit thousand" after a tens prefix, indexed by the unit digit (1..9).
_THOUSANDS_AFTER_TENS = [
    "", "ഒന്നായിരം", "രണ്ടായിരം", "മൂവായിരം", "നാലായിരം", "അയ്യായിരം",
    "ആറായിരം", "ഏഴായിരം", "എട്ടായിരം", "ഒൻപതിനായിരം",
]
_LAKH = "ലക്ഷം"
_ONE_BEFORE_LAKH = "ഒരു"
_DECIMAL_POINT = "ദശാംശം"
_PERCENT = "ശതമാനം"

_VIRAMA = "്"
_ANUSVARA = "ം"
_GLIDE_YA = "യ"
_AYYAYIRAM = "അയ്യായിരം"
# Independent vowel letter -> dependent vowel sign (inherent "a" has none).
_VOWEL_SIGNS = {
    "അ": "", "ആ": "ാ", "ഇ": "ി", "ഈ": "ീ", "ഉ": "ു", "ഊ": "ൂ",
    "എ": "െ", "ഏ": "േ", "ഐ": "ൈ", "ഒ": "ൊ", "ഓ": "ോ", "ഔ": "ൗ",
}
_GEMINATING = ("പ", "ത", "ക", "ച")


def _join(prefix: str, word: str) -> str:
    """Attach ``word`` to a ``prefix`` that ends in the vowel sign i, with sandhi."""
    if word.startswith(_AYYAYIRAM):
        return prefix[:-1] + word[1:]
    first = word[0]
    if first in _VOWEL_SIGNS:
        return prefix + _GLIDE_YA + _VOWEL_SIGNS[first] + word[1:]
    if first in _GEMINATING:
        return prefix + first + _VIRAMA + word
    return prefix + word


def _linking(word: str) -> str:
    """Form used when more number words follow: final ``-m`` becomes ``-tti``."""
    return word[: -len(_ANUSVARA)] + "ത്തി" if word.endswith(_ANUSVARA) else word


def _below_100(n: int) -> str:
    if n < 10:
        return _UNITS[n]
    if n < 20:
        return _TEENS[n - 10]
    tens, unit = divmod(n, 10)
    return _TENS[tens] if unit == 0 else _join(_TENS_PREFIX[tens], _UNITS[unit])


def _below_1000(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    if hundreds == 0:
        return _below_100(rest)
    if rest == 0:
        return _HUNDREDS[hundreds]
    return _join(_HUNDREDS_PREFIX[hundreds], _below_100(rest))


def _thousands_word(n: int) -> str:
    """Spoken form of ``n`` thousand for n in 1..99."""
    if n < 20:
        return _THOUSANDS_SMALL[n]
    tens, unit = divmod(n, 10)
    if unit == 0:
        return _THOUSANDS_TENS[tens]
    return _join(_TENS_PREFIX[tens], _THOUSANDS_AFTER_TENS[unit])


def cardinal(n: int) -> str:
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("cardinal() needs an int")
    if n < 0 or n > 999999:
        raise ValueError("cardinal() supports 0..999999")
    if n == 0:
        return _ZERO
    if n < 1000:
        return _below_1000(n)

    lakhs, rest = divmod(n, 100000)
    thousands, units = divmod(rest, 1000)
    parts: list[str] = []
    if lakhs:
        head = _ONE_BEFORE_LAKH if lakhs == 1 else _below_100(lakhs)
        parts.append(f"{head} {_linking(_LAKH) if rest else _LAKH}")
    if thousands:
        word = _thousands_word(thousands)
        parts.append(_linking(word) if units else word)
    if units:
        parts.append(_below_1000(units))
    return " ".join(parts)


def decimal(int_part: int, frac_digits: str) -> str:
    digits = [_ZERO if int(ch) == 0 else _UNITS[int(ch)] for ch in frac_digits]
    return " ".join([cardinal(int_part), _DECIMAL_POINT, *digits])


def percent(raw: str) -> str:
    if "." in raw:
        whole, frac = raw.split(".", 1)
        body = decimal(int(whole), frac)
    else:
        body = cardinal(int(raw))
    return f"{body} {_PERCENT}"
