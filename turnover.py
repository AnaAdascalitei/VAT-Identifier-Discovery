"""
Estimate, per company, whether VAT registration is EXPECTED, using the turnover
reported in its latest accounts at Companies House (iXBRL), plus its SIC code.

Why: "VAT number not found" is only a failure if the company should have one.
UK registration is compulsory above GBP 90,000 of taxable turnover (since April 2024).
Below that, voluntary registration is common (especially B2B), so 'below_threshold'
means LESS likely, not 'not registered'.

Source: Companies House public API (free key: developer.company-information.service.gov.uk)
  1. /company/{n}/filing-history?category=accounts  -> latest accounts filing
  2. document API metadata                            -> is an iXBRL (xhtml) version available?
  3. document API content                             -> parse turnover from ix:nonFraction tags

Big caveat (and a finding in itself): micro-entity and most small-company accounts
are filed WITHOUT a profit and loss account, so turnover is simply not published
for the majority of companies. Those rows come out as 'turnover_not_published'.

Usage:
  PowerShell:  $env:CH_API_KEY="your-key"
  python turnover.py samples/sample_C_known_reg.csv --out results/turnover_C.csv
"""
from __future__ import annotations

import argparse
import html
import os
import re
import time

import pandas as pd
import requests

API = "https://api.company-information.service.gov.uk"
THRESHOLD = 90_000
DELAY = 0.55            # CH allows 600 requests / 5 minutes per key

# SIC prefixes where much of the activity is VAT-EXEMPT (finance, insurance,
# property letting, education, health, social care). High turnover there does
# not imply registration.
EXEMPT_SIC = ("64", "65", "66", "68", "85", "86", "87", "88")
HOLDING_SIC = ("64201", "64202", "64203", "64204", "64205", "64209", "70100")
TURNOVER_TAGS = re.compile(r":(TurnoverRevenue|Turnover|Revenue|TurnoverGrossOperatingRevenue)$",
                           re.IGNORECASE)


# --------------------------------------------------------------------------- #
# iXBRL parsing (no XML library needed; iXBRL is often not well-formed XML)
# --------------------------------------------------------------------------- #
# Namespace prefixes vary between filing software ("xbrli:", "xbrl:", none), so
# match any prefix instead of assuming "xbrli:".
P = r"(?:[\w-]+:)?"
CTX_RE = re.compile(rf"<{P}context\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</{P}context>",
                    re.IGNORECASE | re.DOTALL)
END_RE = re.compile(rf"<{P}(?:endDate|instant)>\s*([\d-]+)\s*<", re.IGNORECASE)
START_RE = re.compile(rf"<{P}startDate>\s*([\d-]+)\s*<", re.IGNORECASE)
SEG_RE = re.compile(rf"<{P}(?:segment|scenario)\b", re.IGNORECASE)
NF_RE = re.compile(rf"<{P}nonFraction\b([^>]*)>(.*?)</{P}nonFraction>", re.IGNORECASE | re.DOTALL)
ATTR_RE = re.compile(r"([\w:.-]+)\s*=\s*\"([^\"]*)\"")


def _num(text: str, attrs: dict) -> float | None:
    t = re.sub(r"<[^>]+>", "", text).strip()
    fmt = attrs.get("format", "").lower()
    if "zero" in fmt or t in ("-", "—", "–", ""):
        return 0.0
    if "comma" in fmt and "decimal" in fmt and "numcommadecimal" in fmt.replace("-", ""):
        t = t.replace(".", "").replace(" ", "").replace(",", ".")
    else:
        t = t.replace(",", "").replace(" ", "")
    try:
        v = float(t)
    except ValueError:
        return None
    v *= 10 ** int(attrs.get("scale", "0") or 0)
    return -v if attrs.get("sign") == "-" else v


def parse_turnover(ixbrl: str) -> dict:
    """Return {'turnover', 'period_start', 'period_end', 'tag'} for the latest
    whole-entity period (contexts with a <segment> / dimension are skipped)."""
    plain, dimensional = {}, {}
    for cid, body in CTX_RE.findall(ixbrl):
        end = END_RE.search(body)
        start = START_RE.search(body)
        if not end:
            continue
        span = (start.group(1) if start else "", end.group(1))
        if SEG_RE.search(body):
            # Group accounts often tag consolidated figures with a dimension
            # (e.g. "consolidated" vs "company only"). Keep those as a fallback.
            if re.search(r"consolidat|group", body, re.IGNORECASE):
                dimensional[cid] = span
        else:
            plain[cid] = span
    result = _pick(ixbrl, plain)
    if result is None:
        result = _pick(ixbrl, dimensional)
        if result:
            result["tag"] += " [consolidated dimension]"
    if result is None:
        result = text_turnover(ixbrl)
    return result or {"turnover": None, "period_start": "", "period_end": "", "tag": ""}


# Fallback for filings whose software did not tag turnover at all (seen in a
# CCH-generated filing: balance sheet tagged, turnover left as plain text).
# Looks for the income statement line "Turnover [note no.] <current> <prior>".
INCOME_HEAD = re.compile(r"(statement of comprehensive income|income statement|"
                         r"profit and loss account|statement of profit or loss)", re.IGNORECASE)
TEXT_LINE = re.compile(r"\b(Turnover|Revenue)\s+(?:\d{1,2}\s+)?(?:£\s*)?(\d{1,3}(?:,\d{3})+)\b")


def text_turnover(ixbrl: str) -> dict | None:
    txt = re.sub(r"(?is)<(script|style).*?</\1>", " ", ixbrl)
    txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    txt = re.sub(r"\s+", " ", txt)
    for head in INCOME_HEAD.finditer(txt):
        m = TEXT_LINE.search(txt, head.end(), head.end() + 600)
        if m:
            return {"turnover": float(m.group(2).replace(",", "")), "period_start": "",
                    "period_end": "", "tag": f"text:{m.group(1)} (untagged)"}
    return None


def _pick(ixbrl: str, contexts: dict) -> dict | None:
    best = None
    for raw_attrs, inner in NF_RE.findall(ixbrl):
        attrs = {k.lower().split(":")[-1] if k.lower() != "contextref" else "contextref": v
                 for k, v in ATTR_RE.findall(raw_attrs)}
        name = dict(ATTR_RE.findall(raw_attrs)).get("name", "")
        if not TURNOVER_TAGS.search(name):
            continue
        ctx = contexts.get(attrs.get("contextref", ""))
        if not ctx:
            continue
        v = _num(inner, attrs)
        if v is None:
            continue
        if best is None or ctx[1] > best["period_end"]:
            best = {"turnover": v, "period_start": ctx[0], "period_end": ctx[1], "tag": name}
    return best


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
HOLDING_NAME = re.compile(r"\b(HOLDINGS?|TOPCO|MIDCO|BIDCO|HOLDCO)\b", re.IGNORECASE)


def classify(turnover: float | None, sic: str, account_cat: str,
             name: str = "", tag: str = "") -> str:
    sic = (sic or "").strip()
    cat = (account_cat or "").upper()
    if cat == "DORMANT" or sic.startswith("99999"):
        return "not_expected_dormant"
    if turnover is None:
        return "turnover_not_published"
    if turnover >= THRESHOLD:
        # Group accounts of a holding company report the GROUP's consolidated
        # turnover. The sales are made by subsidiaries, which hold the VAT
        # number (or share a group VAT number). Found in the sample: Mediplus,
        # Polarseal, Frank Key, Tibbetts Holdings.
        if cat == "GROUP" or "consolidated" in tag or HOLDING_NAME.search(name) \
                or sic.startswith(HOLDING_SIC):
            return "group_turnover_holding_uncertain"
        if not re.match(r"\d{5}", sic):
            # LLPs and some other entities are not required to supply a SIC code
            # ("None Supplied"), so we cannot rule out an exempt activity.
            # Found in the sample: ZERO THREE CARE HOMES LLP (care = VAT-exempt).
            return "uncertain_no_sic"
        return "uncertain_exempt_sector" if sic.startswith(EXEMPT_SIC) else "expected_registered"
    if sic.startswith(HOLDING_SIC):
        return "holding_low_turnover"
    return "below_threshold_voluntary_possible"


# --------------------------------------------------------------------------- #
# Companies House API
# --------------------------------------------------------------------------- #
class CH:
    def __init__(self, key: str):
        self.s = requests.Session()
        self.s.auth = (key, "")

    def get(self, url: str, **kw):
        time.sleep(DELAY)
        return self.s.get(url, timeout=30, **kw)

    def latest_accounts_ixbrl(self, crn: str) -> tuple[str | None, str]:
        r = self.get(f"{API}/company/{crn}/filing-history",
                     params={"category": "accounts", "items_per_page": 5})
        if r.status_code != 200:
            return None, f"filing_history_{r.status_code}"
        for item in r.json().get("items", []):
            # Only full annual accounts ("AA"). AA01/AA02 etc. are changes of
            # accounting reference date / dormant notices, not accounts.
            if item.get("type") != "AA":
                continue
            meta_url = item.get("links", {}).get("document_metadata")
            if not meta_url:
                continue
            m = self.get(meta_url)
            if m.status_code != 200:
                continue
            resources = m.json().get("resources", {})
            if "application/xhtml+xml" not in resources:
                return None, f"no_ixbrl({item.get('type', '')})"     # e.g. scanned PDF
            c = self.get(meta_url.rstrip("/") + "/content",
                         headers={"Accept": "application/xhtml+xml"}, allow_redirects=True)
            if c.status_code != 200:
                return None, f"content_{c.status_code}"
            return c.text, item.get("date", "")
        return None, "no_accounts_filed"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sample_csv")
    ap.add_argument("--out", default="results/turnover.csv")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--save-docs", default="results/ixbrl",
                    help="folder where every downloaded iXBRL document is kept (evidence + debugging)")
    a = ap.parse_args()
    key = os.environ.get("CH_API_KEY")
    if not key:
        raise SystemExit("Set CH_API_KEY (free key from developer.company-information.service.gov.uk)")

    rows = pd.read_csv(a.sample_csv, dtype=str).fillna("").to_dict("records")
    rows = rows[: a.limit] if a.limit else rows
    ch, out = CH(key), []
    for i, r in enumerate(rows, 1):
        crn = r["CompanyNumber"]
        try:
            doc, note = ch.latest_accounts_ixbrl(crn)
        except requests.RequestException as e:
            doc, note = None, type(e).__name__
        t = parse_turnover(doc) if doc else {"turnover": None, "period_start": "",
                                             "period_end": "", "tag": ""}
        if doc and a.save_docs:
            os.makedirs(a.save_docs, exist_ok=True)
            with open(os.path.join(a.save_docs, f"{crn}.html"), "w", encoding="utf-8") as fh:
                fh.write(doc)
        # When no turnover is found in a digital filing, list what revenue-like
        # tags DO exist, so a missing tag name is visible instead of silent.
        t["revenue_like_tags"] = "" if (not doc or t["turnover"] is not None) else ";".join(sorted(set(
            re.findall(r'name="([^"]*(?:[Tt]urnover|[Rr]evenue|[Ss]ales)[^"]*)"', doc))))[:300]
        out.append({"CompanyNumber": crn, "CompanyName": r.get("CompanyName", ""),
                    "Accounts.AccountCategory": r.get("Accounts.AccountCategory", ""),
                    "SICCode.SicText_1": r.get("SICCode.SicText_1", ""),
                    **t, "note": note if not doc else f"filed {note}",
                    "expectation": classify(t["turnover"], r.get("SICCode.SicText_1", ""),
                                            r.get("Accounts.AccountCategory", ""),
                                            r.get("CompanyName", ""), t["tag"])})
        if i % 10 == 0:
            print(f"{i}/{len(rows)}", flush=True)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    df = pd.DataFrame(out)
    df.to_csv(a.out, index=False)
    print("\nexpectation:\n" + df["expectation"].value_counts().to_string())
    print(f"\nwritten: {a.out}")


if __name__ == "__main__":
    main()