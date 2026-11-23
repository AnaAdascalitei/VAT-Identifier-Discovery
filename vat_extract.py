"""
Extract UK VAT numbers, EORI numbers and Companies House numbers from page text.

Design choices (each one is a defence against false positives):
  * A bare 9-digit number is NOT accepted as a VAT number. It must sit within
    CONTEXT_CHARS of the word "VAT" (or be a GB...000 EORI next to "EORI").
    Without this, phone numbers, order IDs and postcodes flood the output.
  * Every candidate is checked against the HMRC modulus-97 checksum
    (both the original scheme and the post-2010 "9755" scheme) BEFORE it is
    ever sent to HMRC. Rejected candidates are kept, flagged, for the audit log.
  * GD/HA numbers (government departments / health authorities) are recognised
    but have no checksum; they are flagged rather than silently dropped.
  * Every hit carries the surrounding snippet so a human can audit it later.

Usage:
    python vat_extract.py page.txt            # prints JSON lines
    from vat_extract import extract           # use as a library
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict

CONTEXT_CHARS = 40          # how far "VAT" may be from the number
SNIPPET_CHARS = 60          # evidence kept on each side of a hit

WEIGHTS = (8, 7, 6, 5, 4, 3, 2)


# --------------------------------------------------------------------------- #
# Checksum
# --------------------------------------------------------------------------- #
def vat_checksum(vat9: str) -> str | None:
    """Return 'mod97', '9755' or None (invalid) for a 9-digit UK VAT number.

    Weighted sum of the first 7 digits (weights 8..2) plus the last two digits
    taken as a number. Old scheme: total % 97 == 0. New scheme (issued from
    2010): (total + 55) % 97 == 0.
    """
    if not (len(vat9) == 9 and vat9.isdigit()):
        return None
    total = sum(int(d) * w for d, w in zip(vat9[:7], WEIGHTS)) + int(vat9[7:])
    if total % 97 == 0:
        return "mod97"
    if (total + 55) % 97 == 0:
        return "9755"
    return None


# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #
# 9 digits, optionally prefixed GB/XI, optionally grouped 3-4-2, optionally a
# 3-digit branch suffix. Lookarounds stop us from cutting a number out of a
# longer digit run (e.g. an 11-digit phone number).
VAT_RE = re.compile(
    r"(?<![\dA-Z])(?:(GB|XI)[\s\-]?)?"
    r"(\d{3})[\s.\-]?(\d{4})[\s.\-]?(\d{2})"
    r"(?:[\s.\-]?(\d{3}))?(?!\d)",
    re.IGNORECASE,
)
GOV_RE = re.compile(r"(?<![A-Z])(GD|HA)\s?(\d{3})(?!\d)", re.IGNORECASE)
EORI_RE = re.compile(r"(?<![\dA-Z])(GB|XI)(\d{9})000(?!\d)", re.IGNORECASE)
VAT_WORD_RE = re.compile(r"\bVAT\b", re.IGNORECASE)
EORI_WORD_RE = re.compile(r"\bEORI\b", re.IGNORECASE)

# Companies House numbers: 8 digits, or 2-letter prefix + 6 digits
# (SC Scotland, NI Northern Ireland, OC LLP, etc.). Must follow a label.
CRN_RE = re.compile(
    r"(?:company|registered|registration|reg\.?|co\.?)\s*"
    r"(?:in\s+(?:england(?:\s*(?:and|&)\s*wales)?|wales|scotland|northern\s+ireland)[\s,]*)?"
    r"(?:no\.?|number|num\.?|#)?\s*"
    r"(?:\([^)]{0,30}\))?"            # e.g. "(England & Wales)"
    r"[\s:.#\-]*"
    r"(?<![\dA-Z])([A-Z]{2}\d{6}|\d{6,8})(?![\dA-Z])",
    re.IGNORECASE,
)


@dataclass
class Hit:
    kind: str            # 'vat' | 'eori' | 'gov_vat' | 'crn'
    value: str           # normalised identifier (VAT = 9 digits, CRN = 8 chars)
    raw: str             # exactly what was on the page
    checksum: str | None # 'mod97' | '9755' | None ; 'n/a' for crn / gov
    valid_format: bool   # passed every local check
    start: int
    snippet: str


def _snippet(text: str, s: int, e: int) -> str:
    return " ".join(text[max(0, s - SNIPPET_CHARS): e + SNIPPET_CHARS].split())


def _near(word_re: re.Pattern, text: str, s: int, e: int) -> bool:
    window = text[max(0, s - CONTEXT_CHARS): e + 10]
    return bool(word_re.search(window))


def normalise_crn(raw: str) -> str:
    raw = raw.upper()
    return raw if raw[:2].isalpha() else raw.zfill(8)


def extract(text: str) -> list[Hit]:
    hits: list[Hit] = []
    eori_spans: list[tuple[int, int]] = []

    # EORI first: GB + VAT + 000. Accepted only next to the word "EORI".
    for m in EORI_RE.finditer(text):
        if not _near(EORI_WORD_RE, text, m.start(), m.end()):
            continue
        vat9 = m.group(2)
        cs = vat_checksum(vat9)
        hits.append(Hit("eori", vat9, m.group(0), cs, cs is not None,
                        m.start(), _snippet(text, m.start(), m.end())))
        eori_spans.append(m.span())

    for m in VAT_RE.finditer(text):
        if any(a <= m.start() < b for a, b in eori_spans):
            continue
        if not _near(VAT_WORD_RE, text, m.start(), m.end()):
            continue
        vat9 = m.group(2) + m.group(3) + m.group(4)
        cs = vat_checksum(vat9)
        hits.append(Hit("vat", vat9, m.group(0).strip(), cs, cs is not None,
                        m.start(), _snippet(text, m.start(), m.end())))

    for m in GOV_RE.finditer(text):
        if not _near(VAT_WORD_RE, text, m.start(), m.end()):
            continue
        prefix, n = m.group(1).upper(), int(m.group(2))
        ok = (prefix == "GD" and n <= 499) or (prefix == "HA" and n >= 500)
        hits.append(Hit("gov_vat", prefix + m.group(2), m.group(0), "n/a", ok,
                        m.start(), _snippet(text, m.start(), m.end())))

    vat_starts = {h.start for h in hits}
    for m in CRN_RE.finditer(text):
        s = m.start(1)
        # A "Reg No" right after "VAT" is a VAT number, not a company number.
        if s in vat_starts or _near(VAT_WORD_RE, text, s - 5, s - 5):
            continue
        hits.append(Hit("crn", normalise_crn(m.group(1)), m.group(1), "n/a",
                        True, s, _snippet(text, s, m.end(1))))

    # De-duplicate identical identifiers, keep first occurrence.
    seen, out = set(), []
    for h in sorted(hits, key=lambda h: h.start):
        key = (h.kind, h.value)
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


def pairs(hits: list[Hit]) -> list[dict]:
    """(company_number, vat) pairs found on the same page.

    A pair lets us join to Companies House on an exact key instead of a name.
    Only checksum-valid VAT numbers are paired. If a page carries several of
    either, every combination is returned and flagged ambiguous; those need
    review rather than an automatic join.
    """
    crns = [h for h in hits if h.kind == "crn"]
    vats = [h for h in hits if h.kind in ("vat", "eori") and h.valid_format]
    ambiguous = len(crns) > 1 or len(vats) > 1
    return [
        {"company_number": c.value, "vat": v.value, "vat_source": v.kind,
         "distance_chars": abs(c.start - v.start), "ambiguous": ambiguous}
        for c in crns for v in vats
    ]


if __name__ == "__main__":
    text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    hs = extract(text)
    for h in hs:
        print(json.dumps(asdict(h), ensure_ascii=False))
    for p in pairs(hs):
        print(json.dumps({"pair": p}))
