"""Urdu (ur) number verbalization — a native backend for text_normalization.

``num2words`` has no Urdu locale, so Urdu text reached the TTS engine with raw
digits, which OmniVoice garbles (1956 was heard as "انیس سو سپنس"); the same
sentence written out in Urdu words came back with 0% CER. This module renders
integers, decimals and percentages the way an Urdu speaker reads them:

  * 0–99 are irregular (55 = پچپن, 56 = چھپن, 49 = انچاس, 99 = ننانوے), so they
    come from a full 100-entry table rather than tens + units rules;
  * hundreds, thousands and lakhs are separate words with Indian grouping
    (10^5 = لاکھ): 150 → ایک سو پچاس, 2026 → دو ہزار چھبیس, 150000 →
    ایک لاکھ پچاس ہزار;
  * 1100–1999 are read in hundreds, as Urdu speakers say years and amounts
    alike: 1956 → انیس سو چھپن, 1500 → پندرہ سو. Outside that range
    (1000, 2000 and up) the ہزار form is the spoken one.

Scope matches the conservative rules of :mod:`services.text_normalization`:
integers 0–999999 (six digits, so کروڑ is out of scope), decimals read as
"<int> اعشاریہ <digit> <digit>…", percentages as "<number> فیصد".

Spelling uses Urdu letters (ی U+06CC, ہ U+06C1, ک U+06A9, ے U+06D2, ھ U+06BE
for aspirates), never their Arabic look-alikes. Pure Python, no dependencies;
output contains no digits.
"""

from __future__ import annotations

# ── Tables ───────────────────────────────────────────────────────────────────

_BELOW_HUNDRED = [
    "صفر", "ایک", "دو", "تین", "چار", "پانچ", "چھ", "سات", "آٹھ", "نو",                      # 0-9
    "دس", "گیارہ", "بارہ", "تیرہ", "چودہ", "پندرہ", "سولہ", "سترہ", "اٹھارہ", "انیس",          # 10-19
    "بیس", "اکیس", "بائیس", "تئیس", "چوبیس", "پچیس", "چھبیس", "ستائیس", "اٹھائیس", "انتیس",   # 20-29
    "تیس", "اکتیس", "بتیس", "تینتیس", "چونتیس", "پینتیس", "چھتیس", "سینتیس", "اڑتیس", "انتالیس",  # 30-39
    "چالیس", "اکتالیس", "بیالیس", "تینتالیس", "چوالیس", "پینتالیس", "چھیالیس", "سینتالیس", "اڑتالیس", "انچاس",  # 40-49
    "پچاس", "اکاون", "باون", "ترپن", "چون", "پچپن", "چھپن", "ستاون", "اٹھاون", "انسٹھ",     # 50-59
    "ساٹھ", "اکسٹھ", "باسٹھ", "ترسٹھ", "چونسٹھ", "پینسٹھ", "چھیاسٹھ", "سڑسٹھ", "اڑسٹھ", "انہتر",  # 60-69
    "ستر", "اکہتر", "بہتر", "تہتر", "چوہتر", "پچہتر", "چھہتر", "ستتر", "اٹھتر", "اناسی",     # 70-79
    "اسی", "اکیاسی", "بیاسی", "تراسی", "چوراسی", "پچاسی", "چھیاسی", "ستاسی", "اٹھاسی", "نواسی",  # 80-89
    "نوے", "اکانوے", "بانوے", "ترانوے", "چورانوے", "پچانوے", "چھیانوے", "ستانوے", "اٹھانوے", "ننانوے",  # 90-99
]
assert len(_BELOW_HUNDRED) == 100

HUNDRED = "سو"
THOUSAND = "ہزار"
LAKH = "لاکھ"
DECIMAL_WORD = "اعشاریہ"
PERCENT_WORD = "فیصد"


# ── Building blocks ──────────────────────────────────────────────────────────

def _below_thousand(n: int) -> str:
    """0-999: "<n> سو <rest>" (150 → ایک سو پچاس, 900 → نو سو)."""
    if n < 100:
        return _BELOW_HUNDRED[n]
    hundreds, rest = divmod(n, 100)
    head = f"{_BELOW_HUNDRED[hundreds]} {HUNDRED}"
    return f"{head} {_BELOW_HUNDRED[rest]}" if rest else head


# ── Public API ───────────────────────────────────────────────────────────────

def cardinal(n: int) -> str:
    """Render an integer 0 ≤ n ≤ 999999 as Urdu words."""
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("cardinal() expects an int")
    if n < 0 or n > 999_999:
        raise ValueError("cardinal() supports 0..999999")
    if n < 1000:
        return _below_thousand(n)
    if 1100 <= n <= 1999:
        # Years and amounts alike: 1956 → انیس سو چھپن, 1500 → پندرہ سو.
        hundreds, rest = divmod(n, 100)
        head = f"{_BELOW_HUNDRED[hundreds]} {HUNDRED}"
        return f"{head} {_BELOW_HUNDRED[rest]}" if rest else head
    lakhs, rest = divmod(n, 100_000)
    thousands, units = divmod(rest, 1000)
    parts: list[str] = []
    if lakhs:
        parts.append(f"{_BELOW_HUNDRED[lakhs]} {LAKH}")
    if thousands:
        parts.append(f"{_BELOW_HUNDRED[thousands]} {THOUSAND}")
    if units:
        parts.append(_below_thousand(units))
    return " ".join(parts)


def decimal(int_part: int, frac_digits: str) -> str:
    """3.14 → تین اعشاریہ ایک چار (fraction digits are read one by one)."""
    digits = " ".join(_BELOW_HUNDRED[int(d)] for d in frac_digits)
    return f"{cardinal(int_part)} {DECIMAL_WORD} {digits}"


def percent(raw: str) -> str:
    """'50' → پچاس فیصد; '2.5' → دو اعشاریہ پانچ فیصد."""
    if "." in raw:
        a, b = raw.split(".", 1)
        body = decimal(int(a), b)
    else:
        body = cardinal(int(raw))
    return f"{body} {PERCENT_WORD}"
