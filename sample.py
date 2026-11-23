"""
Draw the evaluation samples from the Companies House bulk file.

Input : BasicCompanyDataAsOneFile-YYYY-MM-DD.csv  (Companies House free data product)
Output: samples/sample_A_random.csv      - simple random sample of all ACTIVE companies
        samples/sample_B_stratified.csv  - equal-size draws per accounts stratum (dormant excluded)
        samples/sample_C_known_reg.csv   - FULL / GROUP / MEDIUM filers: very likely VAT-registered,
                                           so "not found" here is a recall failure, not a true negative
        samples/population_strata.csv    - stratum sizes in the population (for reweighting B)
        samples/README_sample.txt        - seed, snapshot, sizes: everything needed to reproduce

Why three samples:
  A answers "what coverage would the customer actually see?"
  B answers "where does the method work and where does it fail?"
  C gives a denominator where the true answer is (almost) always "has a VAT number",
    which is the only way to estimate recall without a reference dataset.

Usage:
  python sample.py BasicCompanyDataAsOneFile-2026-11-01.csv --seed 42
"""
from __future__ import annotations

import argparse
import math
import os
from datetime import date

import pandas as pd

# Map Companies House 'Accounts.AccountCategory' values to analysis strata.
# Anything unmapped lands in 'other' so nothing disappears silently.
STRATA = {
    "DORMANT": "dormant",
    "MICRO ENTITY": "micro",
    "TOTAL EXEMPTION SMALL": "small",
    "TOTAL EXEMPTION FULL": "small",
    "SMALL": "small",
    "UNAUDITED ABRIDGED": "small",
    "AUDITED ABRIDGED": "small",
    "PARTIAL EXEMPTION": "small",
    "MEDIUM": "large",
    "FULL": "large",
    "GROUP": "large",
    "AUDIT EXEMPTION SUBSIDIARY": "subsidiary",
    "FILING EXEMPTION SUBSIDIARY": "subsidiary",
    "NO ACCOUNTS FILED": "no_accounts",
    "ACCOUNTS TYPE NOT AVAILABLE": "no_accounts",
    "INITIAL": "no_accounts",
}
KNOWN_REG = {"FULL", "GROUP", "MEDIUM"}
DORMANT_SIC = "99999"

KEEP_COLS = [
    "CompanyName", "CompanyNumber", "CompanyStatus", "CompanyCategory",
    "IncorporationDate", "Accounts.AccountCategory", "SICCode.SicText_1",
    "RegAddress.PostTown", "RegAddress.PostCode",
]


def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, low_memory=False)
    df.columns = df.columns.str.strip()            # CH headers carry leading spaces
    missing = [c for c in KEEP_COLS if c not in df.columns]
    if missing:
        raise SystemExit(f"Columns not found in file: {missing}")
    df = df[KEEP_COLS].copy()
    for c in KEEP_COLS:
        df[c] = df[c].fillna("").str.strip()
    return df


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    active = df[df["CompanyStatus"].str.upper() == "ACTIVE"].copy()
    cat = active["Accounts.AccountCategory"].str.upper()
    active["stratum"] = cat.map(STRATA).fillna("other")
    # Dormant by SIC code even if accounts category says otherwise.
    is_dormant_sic = active["SICCode.SicText_1"].str.startswith(DORMANT_SIC)
    active.loc[is_dormant_sic, "stratum"] = "dormant"
    return active


def margin_of_error(n: int, p: float = 0.5, z: float = 1.96) -> float:
    return z * math.sqrt(p * (1 - p) / n) if n else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-random", type=int, default=200)
    ap.add_argument("--n-per-stratum", type=int, default=50)
    ap.add_argument("--n-known", type=int, default=100)
    ap.add_argument("--out", default="samples")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    active = prepare(load(a.csv))
    pop = active["stratum"].value_counts().rename_axis("stratum").reset_index(name="population")
    pop["share"] = pop["population"] / pop["population"].sum()
    pop.to_csv(f"{a.out}/population_strata.csv", index=False)

    # A: simple random over ALL active companies (dormant included: this is reality).
    A = active.sample(n=min(a.n_random, len(active)), random_state=a.seed)
    A.assign(sample="A").to_csv(f"{a.out}/sample_A_random.csv", index=False)

    # B: equal draws per stratum, dormant excluded (a dormant company is not
    # a trading supplier). Weight = population / sample, used to reweight results.
    B_parts = []
    for s, g in active[active["stratum"] != "dormant"].groupby("stratum"):
        part = g.sample(n=min(a.n_per_stratum, len(g)), random_state=a.seed)
        part = part.assign(weight=len(g) / len(part))
        B_parts.append(part)
    B = pd.concat(B_parts).assign(sample="B")
    B.to_csv(f"{a.out}/sample_B_stratified.csv", index=False)

    # C: large filers. Caveat: small companies may file FULL accounts voluntarily,
    # so "almost all registered" is an assumption to check, not a fact.
    # Use the 'large' stratum, not the raw category, so companies with a dormant
    # SIC code (99999) that still file FULL/GROUP accounts are excluded.
    pool_C = active[(active["stratum"] == "large")
                    & active["Accounts.AccountCategory"].str.upper().isin(KNOWN_REG)]
    C = pool_C.sample(n=min(a.n_known, len(pool_C)), random_state=a.seed)
    C.assign(sample="C").to_csv(f"{a.out}/sample_C_known_reg.csv", index=False)

    overlap = len(set(A.CompanyNumber) & set(B.CompanyNumber) | set(A.CompanyNumber) & set(C.CompanyNumber))
    lines = [
        f"source file      : {os.path.basename(a.csv)}",
        f"drawn on         : {date.today().isoformat()}",
        f"seed             : {a.seed}",
        f"active companies : {len(active):,}",
        f"sample A (random): {len(A)}  (95% margin of error +/- {margin_of_error(len(A)):.1%})",
        f"sample B (strat.): {len(B)}  ({B['stratum'].nunique()} strata x up to {a.n_per_stratum})",
        f"sample C (known) : {len(C)}  (pool {len(pool_C):,}; +/- {margin_of_error(len(C)):.1%})",
        f"A overlapping B/C: {overlap}",
        "",
        "population by stratum:",
        pop.to_string(index=False),
    ]
    open(f"{a.out}/README_sample.txt", "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()