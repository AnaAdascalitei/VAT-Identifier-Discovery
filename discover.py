"""
Find candidate websites for each company automatically, instead of by hand.

Two sources, both legitimate and logged:

  wikidata  (free, no key)
      Wikidata links many UK companies' Companies House number to their official
      website. One SPARQL query covers the whole sample. Coverage is mostly
      large / well-known companies, so it helps sample C far more than A.

  brave     (optional; set BRAVE_API_KEY. Brave's monthly free credit covers
             roughly a thousand queries, enough for this sample)
      Two queries per company:
        "<company number>"            pages that print the registration number,
                                      which is what company footers do
        "<company name>" <postcode>   fallback for sites without the number
      Directory sites (Companies House, Endole, OpenCorporates, LinkedIn...) are
      dropped: they print every company's number and are never the company's site.

Search results are only CANDIDATES. find_sites.py still has to prove each site
belongs to the company (its own number on the site, or name + postcode).

Output:
  candidates.csv            CompanyNumber,domain,source,rank
  discover_log.jsonl        every query and every raw result, for the audit trail

Usage:
  python discover.py samples/sample_C_known_reg.csv               # wikidata only
  set BRAVE_API_KEY=...   (PowerShell: $env:BRAVE_API_KEY="...")
  python discover.py samples/sample_C_known_reg.csv --brave
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from urllib.parse import urlparse

import pandas as pd
import requests

from find_sites import SKIP_HOSTS, USER_AGENT

WIKIDATA = "https://query.wikidata.org/sparql"
BRAVE = "https://api.search.brave.com/res/v1/web/search"
OFFICIAL_WEBSITE = "P856"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def host_of(url: str) -> str:
    h = urlparse(url if "://" in url else "http://" + url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def keep_host(h: str) -> bool:
    return bool(h) and not SKIP_HOSTS.search(h)


class Log:
    def __init__(self, path: str):
        self.f = open(path, "a", encoding="utf-8")

    def __call__(self, **rec):
        self.f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.f.flush()


# --------------------------------------------------------------------------- #
# Wikidata
# --------------------------------------------------------------------------- #
def sparql(session: requests.Session, q: str) -> list[dict]:
    r = session.get(WIKIDATA, params={"query": q},
                    headers={"Accept": "application/sparql-results+json"}, timeout=60)
    r.raise_for_status()
    return r.json()["results"]["bindings"]


def find_ch_property(session: requests.Session) -> str:
    """Look the property up by its label instead of hard-coding an ID."""
    q = """SELECT ?p WHERE { ?p a wikibase:Property ; rdfs:label ?l .
             FILTER(LANG(?l)="en" && LCASE(STR(?l))="companies house company id") } LIMIT 1"""
    rows = sparql(session, q)
    if not rows:
        raise RuntimeError("Companies House property not found on Wikidata")
    return rows[0]["p"]["value"].rsplit("/", 1)[-1]


def parse_wikidata(rows: list[dict]) -> list[tuple[str, str]]:
    out = []
    for r in rows:
        crn = r["crn"]["value"].strip().upper()
        crn = crn.zfill(8) if crn.isdigit() else crn
        out.append((crn, r["site"]["value"]))
    return out


def wikidata_sites(session, crns: list[str], log: Log, batch: int = 200) -> list[tuple[str, str]]:
    prop = find_ch_property(session)
    log(source="wikidata", event="property", value=prop)
    found = []
    for i in range(0, len(crns), batch):
        chunk = crns[i:i + batch]
        # Wikidata sometimes stores the number without leading zeros: ask for both.
        variants = sorted({c for x in chunk for c in (x, x.lstrip("0"))})
        values = " ".join(json.dumps(v) for v in variants)
        q = f"""SELECT ?crn ?site WHERE {{ VALUES ?crn {{ {values} }}
                  ?item wdt:{prop} ?crn ; wdt:{OFFICIAL_WEBSITE} ?site . }}"""
        rows = sparql(session, q)
        log(source="wikidata", event="batch", n_query=len(chunk), n_rows=len(rows),
            rows=[{k: v["value"] for k, v in r.items()} for r in rows])
        found += parse_wikidata(rows)
        time.sleep(1)
    return found


# --------------------------------------------------------------------------- #
# Brave
# --------------------------------------------------------------------------- #
def parse_brave(payload: dict, top: int) -> list[str]:
    hosts = []
    for res in payload.get("web", {}).get("results", []):
        h = host_of(res.get("url", ""))
        if keep_host(h) and h not in hosts:
            hosts.append(h)
        if len(hosts) >= top:
            break
    return hosts


def brave_sites(session, rows: list[dict], log: Log, top: int, delay: float) -> list[tuple]:
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        raise SystemExit("BRAVE_API_KEY is not set")
    out = []
    for i, r in enumerate(rows, 1):
        crn, name = r["CompanyNumber"], r["CompanyName"]
        pc = r.get("RegAddress.PostCode", "")
        for label, q in (("brave_crn", f'"{crn}"'), ("brave_name", f'"{name}" {pc}'.strip())):
            try:
                resp = session.get(BRAVE, params={"q": q, "country": "gb", "count": 10},
                                   headers={"X-Subscription-Token": key,
                                            "Accept": "application/json"}, timeout=20)
                payload = resp.json() if resp.ok else {}
                status = resp.status_code
            except requests.RequestException as e:
                payload, status = {}, type(e).__name__
            hosts = parse_brave(payload, top)
            log(source=label, crn=crn, query=q, status=status,
                urls=[x.get("url") for x in payload.get("web", {}).get("results", [])],
                kept=hosts)
            out += [(crn, h, label, rank) for rank, h in enumerate(hosts, 1)]
            time.sleep(delay)
        if i % 10 == 0:
            print(f"brave {i}/{len(rows)}", flush=True)
    return out


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sample_csv", nargs="+")
    ap.add_argument("--brave", action="store_true")
    ap.add_argument("--no-wikidata", action="store_true")
    ap.add_argument("--top", type=int, default=3, help="hosts kept per search query")
    ap.add_argument("--delay", type=float, default=1.1, help="seconds between Brave calls")
    ap.add_argument("--out", default="candidates.csv")
    ap.add_argument("--log", default="discover_log.jsonl")
    a = ap.parse_args()

    rows = pd.concat([pd.read_csv(p, dtype=str) for p in a.sample_csv]).fillna("")
    rows = rows.drop_duplicates("CompanyNumber").to_dict("records")
    crns = [r["CompanyNumber"] for r in rows]
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    log = Log(a.log)

    found: list[tuple] = []
    if not a.no_wikidata:
        wd = wikidata_sites(s, crns, log)
        found += [(c, host_of(u), "wikidata", 1) for c, u in wd if c in set(crns)]
        print(f"wikidata: {len({c for c, *_ in found})} of {len(crns)} companies have a website")
    if a.brave:
        found += brave_sites(s, rows, log, a.top, a.delay)

    cols = ["CompanyNumber", "domain", "source", "rank"]
    df = pd.DataFrame(found, columns=cols)
    if df.empty:
        # Nothing found: still write a header-only file so find_sites.py can read it.
        df.to_csv(a.out, index=False)
        print(f"0 candidate sites -> {a.out}")
        return
    df = df[df["domain"].map(keep_host).astype(bool)].drop_duplicates(["CompanyNumber", "domain"])
    order = {"wikidata": 0, "brave_crn": 1, "brave_name": 2}
    df = df.sort_values(["CompanyNumber", "source", "rank"], key=lambda c: c.map(order)
                        if c.name == "source" else c)
    df.to_csv(a.out, index=False)
    print(f"{len(df)} candidate sites for {df.CompanyNumber.nunique()} companies -> {a.out}")
    print(df.groupby("source").CompanyNumber.nunique().rename("companies").to_string())


if __name__ == "__main__":
    main()