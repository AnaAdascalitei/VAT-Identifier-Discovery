"""
Turn a hand-annotated list (CompanyName,domain,note) into manual_domains.csv
(CompanyNumber,domain,source,note) by matching names against a sample file,
and summarise the annotation notes.

Usage:
  python add_numbers.py manual_by_name.csv samples/sample_C_known_reg.csv
"""
import re
import sys

import pandas as pd


def key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(name).upper().replace("LIMITED", "LTD"))


def main(annot_path: str, sample_path: str, out: str = "manual_domains.csv") -> None:
    ann = pd.read_csv(annot_path, dtype=str).fillna("")
    smp = pd.read_csv(sample_path, dtype=str).fillna("")
    lookup = {key(n): c for n, c in zip(smp.CompanyName, smp.CompanyNumber)}

    ann["CompanyNumber"] = ann.CompanyName.map(lambda n: lookup.get(key(n), ""))
    missing = ann[ann.CompanyNumber == ""]
    if len(missing):
        print("NOT matched to the sample (fix the name in the file):")
        print(missing[["CompanyName", "domain", "note"]].to_string(index=False), "\n")

    ann["source"] = "manual_ai_search"
    ann[["CompanyNumber", "domain", "source", "note"]].to_csv(out, index=False)

    ok = ann[ann.CompanyNumber != ""]
    print(f"annotated companies: {len(ok)}")
    print(f"with a domain      : {(ok.domain != '').sum()}")
    print("\nby category:")
    print(ok.note.value_counts().to_string())
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main(*sys.argv[1:])
