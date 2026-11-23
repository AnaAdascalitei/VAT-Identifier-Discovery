"""
Company -> website -> VAT candidates.

For every company in a sample CSV (output of sample.py):
  1. Build candidate domains:
       a) from manual_domains.csv, if you found the site by hand (always tried first), and
       b) guessed from the registered name (acmewidgets.co.uk, acme-widgets.com, ...).
  2. Fetch the homepage; follow links whose text/URL looks like contact / about / terms /
     privacy / legal (max MAX_PAGES pages per site). robots.txt is respected.
  3. Run vat_extract on every page.
  4. Decide whether the site really belongs to THIS company. Evidence levels:
       crn            - the company's own Companies House number appears on the site (strong)
       name_postcode  - every significant name token AND the registered postcode appear (medium)
       name_only      - name tokens appear, postcode does not (weak: NOT accepted)
       none           - nothing links site to company (rejected)
     Only 'crn' and 'name_postcode' sites can produce an accepted VAT number.
     This is the main defence against attaching a real VAT number to the wrong company.
  5. Pick at most one VAT number per company, or mark the company ambiguous.

Outputs (in --out):
  results_<sample>.csv   one row per company, with status and evidence
  fetch_log_<sample>.csv every HTTP request made (url, status, bytes) - your audit trail

Usage:
  python find_sites.py samples/sample_C_known_reg.csv --manual manual_domains.csv --workers 8
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import pandas as pd
import requests

from vat_extract import extract

USER_AGENT = "VAT-research-prototype/0.1 (student project; contact: your-email@example.com)"
TIMEOUT = 8
DELAY = 0.7                  # seconds between requests to the same site
MAX_PAGES = 6                # pages fetched per candidate site, homepage included
MAX_BYTES = 1_500_000
TLDS = (".co.uk", ".com", ".uk")
LEGAL_HINTS = re.compile(
    r"contact|about|terms|conditions|privacy|legal|imprint|company[-_ ]?info|cookie",
    re.IGNORECASE)

SUFFIXES = {"LIMITED", "LTD", "PLC", "LLP", "LP", "CIC", "COMPANY", "CO", "THE"}
GENERIC = {"UK", "GB", "GROUP", "HOLDINGS", "SERVICES", "SOLUTIONS", "INTERNATIONAL",
           "ENTERPRISES", "TRADING", "SVCS"}


# --------------------------------------------------------------------------- #
# Name handling
# --------------------------------------------------------------------------- #
def name_tokens(name: str) -> list[str]:
    name = name.upper().replace("&", " AND ")
    name = re.sub(r"\(.*?\)", " ", name)               # "(UK)", "(2019)"
    toks = re.findall(r"[A-Z0-9]+", name)
    return [t for t in toks if t not in SUFFIXES]


def guess_domains(name: str) -> list[str]:
    toks = name_tokens(name)
    if not toks:
        return []
    core = [t for t in toks if t not in GENERIC and t != "AND"] or toks
    stems = []
    for parts in (toks, core):
        for joiner in ("", "-"):
            s = joiner.join(parts).lower()
            if 3 <= len(s) <= 40 and s not in stems:
                stems.append(s)
    return [s + tld for s in stems for tld in TLDS]


def norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


# --------------------------------------------------------------------------- #
# HTML -> text + links (stdlib only)
# --------------------------------------------------------------------------- #
class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.links, self._skip, self._a = [], [], 0, None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        elif tag == "a":
            self._a = [dict(attrs).get("href") or "", ""]
        elif tag in ("br", "p", "div", "li", "footer", "tr", "td"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1
        elif tag == "a" and self._a:
            self.links.append(tuple(self._a))
            self._a = None

    def handle_data(self, data):
        if self._skip:
            return
        self.text.append(data)
        if self._a is not None:
            self._a[1] += data


def parse_html(html: str) -> tuple[str, list[tuple[str, str]]]:
    p = _Page()
    try:
        p.feed(html)
    except Exception:
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.text))
    return text, p.links


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
class Fetcher:
    def __init__(self, log_path: str):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self._log_lock = threading.Lock()
        self._log = open(log_path, "w", newline="", encoding="utf-8")
        self._w = csv.writer(self._log)
        self._w.writerow(["company_number", "url", "status", "bytes", "note"])
        self._robots: dict[str, RobotFileParser | None] = {}

    def log(self, crn, url, status, n, note=""):
        with self._log_lock:
            self._w.writerow([crn, url, status, n, note])
            self._log.flush()

    def allowed(self, url: str) -> bool:
        root = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if root not in self._robots:
            rp = RobotFileParser()
            try:
                r = self.s.get(root + "/robots.txt", timeout=TIMEOUT)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])
            self._robots[root] = rp
        return self._robots[root].can_fetch(USER_AGENT, url)

    def get(self, crn: str, url: str) -> tuple[str | None, str]:
        """Return (html, final_url) or (None, reason)."""
        if not self.allowed(url):
            self.log(crn, url, "-", 0, "robots_disallow")
            return None, "robots"
        try:
            r = self.s.get(url, timeout=TIMEOUT, allow_redirects=True, stream=True)
            body = r.raw.read(MAX_BYTES, decode_content=True)
            ctype = r.headers.get("Content-Type", "")
            self.log(crn, url, r.status_code, len(body), r.url if r.url != url else "")
            if r.status_code != 200 or "html" not in ctype.lower():
                return None, f"http_{r.status_code}"
            return body.decode(r.encoding or "utf-8", errors="replace"), r.url
        except requests.RequestException as e:
            self.log(crn, url, "ERR", 0, type(e).__name__)
            return None, type(e).__name__


# --------------------------------------------------------------------------- #
# One site
# --------------------------------------------------------------------------- #
def crawl_site(f: Fetcher, crn: str, base: str) -> dict | None:
    if not base.startswith("http"):
        base = "https://" + base
    html, final = f.get(crn, base)
    if html is None and base.startswith("https://"):
        time.sleep(DELAY)
        html, final = f.get(crn, "http://" + base[len("https://"):])
    if html is None:
        return None

    host = urlparse(final).netloc
    pages = {final: html}
    _, links = parse_html(html)
    queue = []
    for href, label in links:
        u = urljoin(final, href).split("#")[0]
        if urlparse(u).netloc != host or u in pages or u in queue:
            continue                       # stay on the company's own site
        if LEGAL_HINTS.search(u) or LEGAL_HINTS.search(label or ""):
            queue.append(u)
    for u in queue[: MAX_PAGES - 1]:
        time.sleep(DELAY)
        h, fu = f.get(crn, u)
        if h is not None:
            pages[fu] = h
    return {"host": host, "pages": {u: parse_html(h)[0] for u, h in pages.items()}}


def site_evidence(site: dict, crn: str, name: str, postcode: str) -> str:
    alltext = "\n".join(site["pages"].values())
    hits = [h for t in site["pages"].values() for h in extract(t)]
    site_crns = {h.value for h in hits if h.kind == "crn"}
    if crn in site_crns:
        return "crn"
    flat = norm(alltext)
    # Generic words ("SERVICES", "GROUP", "UK") are often dropped on websites,
    # so require only the distinctive tokens of the name.
    toks = [t for t in name_tokens(name) if len(t) >= 2 and t != "AND"]
    core = [t for t in toks if t not in GENERIC] or toks
    name_ok = bool(core) and all(t in flat for t in core)
    pc_ok = bool(postcode) and norm(postcode) in flat
    if name_ok and pc_ok:
        return "name_postcode"
    if site_crns:
        # The site names a DIFFERENT registered company: typically a parent
        # group's site (the Subsea 7 case). Never accepted, but counted.
        return "other_crn"
    if name_ok:
        return "name_only"
    return "none"


def choose_vat(site: dict, crn: str) -> tuple[str, list[dict]]:
    """Return (decision, candidates). decision: 'single' | 'ambiguous' | 'none'."""
    cands = []
    for url, text in site["pages"].items():
        hs = extract(text)
        crn_here = any(h.kind == "crn" and h.value == crn for h in hs)
        for h in hs:
            if h.kind in ("vat", "eori", "gov_vat"):
                cands.append({"vat": h.value, "kind": h.kind, "checksum": h.checksum,
                              "valid_format": h.valid_format, "url": url,
                              "same_page_as_crn": crn_here, "snippet": h.snippet})
    valid = {c["vat"] for c in cands if c["valid_format"]}
    if not valid:
        return "none", cands
    if len(valid) == 1:
        return "single", cands
    # Several valid numbers: accept only if exactly one sits on a page that also
    # carries this company's own registration number.
    paired = {c["vat"] for c in cands if c["valid_format"] and c["same_page_as_crn"]}
    return ("single", [c for c in cands if c["vat"] in paired]) if len(paired) == 1 \
        else ("ambiguous", cands)


# --------------------------------------------------------------------------- #
# One company
# --------------------------------------------------------------------------- #
ACCEPTED_EVIDENCE = {"crn", "name_postcode"}


EVIDENCE_RANK = ["none", "other_crn", "name_only", "name_postcode", "crn"]
SKIP_HOSTS = re.compile(
    r"(company-information\.service\.gov\.uk|companieshouse|opencorporates|endole|"
    r"companycheck|bizdb|companiesintheuk|find-and-update|dnb\.com|linkedin|facebook|"
    r"twitter|x\.com|instagram|yell\.com|wikipedia|bloomberg|zoominfo|gov\.uk$)",
    re.IGNORECASE)


def process(f: Fetcher, row: dict, manual: dict[str, list[tuple[str, str]]]) -> dict:
    crn, name = row["CompanyNumber"], row["CompanyName"]
    postcode = row.get("RegAddress.PostCode", "") or ""
    tried, best = [], None
    candidates = manual.get(crn, []) + [(d, "guess") for d in guess_domains(name)]
    for dom, source in candidates:
        key = re.sub(r"^https?://(www\.)?", "", dom).rstrip("/").lower()
        if key in tried or SKIP_HOSTS.search(key):
            continue
        tried.append(key)
        site = crawl_site(f, crn, dom)
        if site is None:
            continue
        ev = site_evidence(site, crn, name, postcode)
        rank = EVIDENCE_RANK.index(ev)
        if best is None or rank > best[0]:
            best = (rank, dom, site, ev, source)
        if ev == "crn":
            break                         # can't do better than this

    out = {k: row.get(k, "") for k in ("sample", "stratum", "CompanyNumber", "CompanyName",
                                       "RegAddress.PostCode", "Accounts.AccountCategory",
                                       "SICCode.SicText_1", "weight")}
    out.update(domains_tried=len(tried), domain="", domain_source="", host="", site_evidence="",
               pages_fetched=0, vat="", vat_kind="", vat_checksum="", vat_url="",
               vat_snippet="", n_candidates=0, candidates="", status="")
    if best is None:
        out["status"] = "no_site_found"
        return out

    _, dom, site, ev, source = best
    out.update(domain=dom, domain_source=source, host=site["host"], site_evidence=ev,
               pages_fetched=len(site["pages"]))
    decision, cands = choose_vat(site, crn)
    out["n_candidates"] = len(cands)
    out["candidates"] = json.dumps(cands, ensure_ascii=False)[:5000]

    if decision == "single":
        c = next(c for c in cands if c["valid_format"])
        out.update(vat=c["vat"], vat_kind=c["kind"], vat_checksum=c["checksum"],
                   vat_url=c["url"], vat_snippet=c["snippet"])
    if ev == "other_crn":
        out["status"] = "group_or_other_company_site"
    elif ev not in ACCEPTED_EVIDENCE:
        # A number on a site we cannot tie to the company is never reported as found.
        out["status"] = "site_unverified" + ("_has_vat" if decision != "none" else "")
    elif decision == "single":
        out["status"] = "vat_candidate"          # still needs HMRC + name check
    elif decision == "ambiguous":
        out["status"] = "vat_ambiguous"
    elif cands:
        out["status"] = "vat_bad_checksum_only"
    else:
        out["status"] = "site_ok_no_vat"
    return out


def load_manual(*paths: str | None) -> dict[str, list[tuple[str, str]]]:
    """CSVs with columns CompanyNumber,domain[,source][,note]. Several rows per company allowed.
    'source' defaults to 'manual'. Files are read in order, so put the most trusted first."""
    m: dict[str, list[tuple[str, str]]] = {}
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        df = pd.read_csv(path, dtype=str).fillna("")
        for r in df.to_dict("records"):
            dom, c = r.get("domain", "").strip(), r.get("CompanyNumber", "").strip()
            if not dom or not c:
                continue
            c = c.zfill(8) if c.isdigit() else c.upper()
            m.setdefault(c, []).append((dom, r.get("source", "").strip() or "manual"))
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sample_csv")
    ap.add_argument("--manual", default="manual_domains.csv")
    ap.add_argument("--candidates", default="candidates.csv",
                    help="output of discover.py (tried after --manual, before guessing)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="only the first N companies (for a dry run)")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    tag = os.path.splitext(os.path.basename(a.sample_csv))[0].replace("sample_", "")
    rows = pd.read_csv(a.sample_csv, dtype=str).fillna("").to_dict("records")
    if a.limit:
        rows = rows[: a.limit]
    manual = load_manual(a.manual, a.candidates)
    f = Fetcher(os.path.join(a.out, f"fetch_log_{tag}.csv"))

    results, t0 = [], time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(process, f, r, manual): r for r in rows}
        for i, fu in enumerate(as_completed(futs), 1):
            try:
                results.append(fu.result())
            except Exception as e:                       # never lose a company silently
                r = futs[fu]
                results.append({"CompanyNumber": r["CompanyNumber"],
                                "CompanyName": r["CompanyName"], "status": f"error_{type(e).__name__}"})
            if i % 10 == 0 or i == len(rows):
                print(f"{i}/{len(rows)}  {time.time() - t0:.0f}s", flush=True)

    df = pd.DataFrame(results)
    path = os.path.join(a.out, f"results_{tag}.csv")
    df.to_csv(path, index=False)
    print("\nstatus counts:")
    print(df["status"].value_counts().to_string())
    print(f"\nwritten: {path}")


if __name__ == "__main__":
    main()