import subprocess
import sys

import pandas as pd

from vat_extract import vat_checksum, extract, pairs

# Real numbers seen during research (all pass HMRC's checksum).
GOOGLE_UK = "765970776"
SPOTON = "930929315"        # from a published website footer example
SNOWBIRD = "927328019"      # on a live site, but HMRC says invalid


def find_9755_example() -> str:
    for n in range(100000000, 100100000):
        s = str(n)
        if vat_checksum(s) == "9755":
            return s
    raise AssertionError


# ---------------- checksum ---------------- #
def test_checksum_known_numbers():
    assert vat_checksum(GOOGLE_UK) == "mod97"
    assert vat_checksum(SPOTON) == "mod97"
    assert vat_checksum(SNOWBIRD) == "mod97"   # => stale/deregistered, not a typo


def test_checksum_rejects_single_digit_typo():
    s = list(GOOGLE_UK)
    s[3] = str((int(s[3]) + 1) % 10)
    assert vat_checksum("".join(s)) is None


def test_checksum_9755_scheme():
    assert vat_checksum(find_9755_example()) == "9755"


def test_checksum_bad_input():
    assert vat_checksum("12345") is None
    assert vat_checksum("abcdefghi") is None


# ---------------- extraction ---------------- #
def vats(text):
    return [(h.value, h.valid_format) for h in extract(text) if h.kind == "vat"]


def test_formats():
    for t in [f"VAT No: GB{GOOGLE_UK}",
              "VAT Reg. No. GB 765 9707 76",
              "VAT number 765-9707-76",
              f"Our VAT registration number is {GOOGLE_UK}."]:
        assert vats(t) == [(GOOGLE_UK, True)], t


def test_branch_suffix():
    assert vats("VAT: GB 765 9707 76 001") == [(GOOGLE_UK, True)]


def test_no_vat_word_no_hit():
    # Order numbers and IDs are 9 digits too. Without "VAT" nearby, ignore.
    assert vats(f"Order reference {GOOGLE_UK} has shipped") == []


def test_phone_number_not_matched():
    assert vats("VAT enquiries: call 020 7946 0958") == []


def test_invalid_checksum_kept_but_flagged():
    assert vats("VAT No: GB123456789") == [("123456789", False)]


def test_eori():
    hs = extract(f"EORI: GB{GOOGLE_UK}000")
    assert [(h.kind, h.value) for h in hs] == [("eori", GOOGLE_UK)]


def test_eori_without_label_ignored_as_eori():
    assert [h for h in extract(f"ref GB{GOOGLE_UK}000") if h.kind == "eori"] == []


def test_gov_numbers():
    hs = extract("VAT No: GD123")
    assert hs[0].kind == "gov_vat" and hs[0].valid_format


def test_company_number_and_pair():
    footer = ("Spoton.net Limited, 167-169 Union Street, Torquay, TQ1 4BX "
              "Company registration number (England & Wales): 06139437 "
              f"VAT Number: {SPOTON} | ICO Registration: ZA343953")
    hs = extract(footer)
    kinds = {(h.kind, h.value) for h in hs}
    assert ("crn", "06139437") in kinds
    assert ("vat", SPOTON) in kinds
    p = pairs(hs)
    assert p == [{"company_number": "06139437", "vat": SPOTON, "vat_source": "vat",
                  "distance_chars": p[0]["distance_chars"], "ambiguous": False}]


def test_scottish_and_padded_crn():
    hs = extract("Registered in Scotland No. SC123456. Company No 123456")
    assert {h.value for h in hs if h.kind == "crn"} == {"SC123456", "00123456"}


def test_vat_reg_no_is_not_a_company_number():
    hs = extract(f"VAT Reg No {GOOGLE_UK}")
    assert [h.kind for h in hs] == ["vat"]


def test_two_vats_marks_pair_ambiguous():
    hs = extract(f"Company No 06139437. VAT: {SPOTON}. Group VAT: {GOOGLE_UK}")
    p = pairs(hs)
    assert len(p) == 2 and all(x["ambiguous"] for x in p)


# ---------------- sampling ---------------- #
def test_sampler(tmp_path):
    cats = (["DORMANT"] * 400 + ["MICRO ENTITY"] * 300 + ["TOTAL EXEMPTION FULL"] * 150
            + ["FULL"] * 60 + ["GROUP"] * 20 + ["NO ACCOUNTS FILED"] * 70)
    n = len(cats)
    df = pd.DataFrame({
        "CompanyName": [f"CO {i} LTD" for i in range(n)],
        " CompanyNumber": [f"{i:08d}" for i in range(n)],      # leading space, as in CH
        "CompanyStatus": ["Active"] * (n - 50) + ["Dissolved"] * 50,
        "CompanyCategory": ["Private Limited Company"] * n,
        "IncorporationDate": ["01/01/2015"] * n,
        "Accounts.AccountCategory": cats,
        "SICCode.SicText_1": ["99999 - Dormant Company" if c == "DORMANT" else "25620 - Machining"
                              for c in cats],
        "RegAddress.PostTown": ["LEEDS"] * n,
        "RegAddress.PostCode": ["LS1 1AA"] * n,
    })
    src = tmp_path / "ch.csv"
    df.to_csv(src, index=False)
    out = tmp_path / "samples"
    r = subprocess.run([sys.executable, "sample.py", str(src), "--seed", "7",
                        "--n-random", "100", "--n-per-stratum", "20", "--n-known", "30",
                        "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    A = pd.read_csv(out / "sample_A_random.csv", dtype=str)
    B = pd.read_csv(out / "sample_B_stratified.csv", dtype=str)
    C = pd.read_csv(out / "sample_C_known_reg.csv", dtype=str)
    assert len(A) == 100 and (A.CompanyStatus == "Active").all()
    assert "dormant" not in set(B.stratum)
    assert B.groupby("stratum").size().max() == 20
    assert set(C["Accounts.AccountCategory"]) <= {"FULL", "GROUP"}

    # Reproducible: same seed -> same draw.
    out2 = tmp_path / "samples2"
    subprocess.run([sys.executable, "sample.py", str(src), "--seed", "7",
                    "--n-random", "100", "--out", str(out2)], check=True, capture_output=True)
    A2 = pd.read_csv(out2 / "sample_A_random.csv", dtype=str)
    assert list(A.CompanyNumber) == list(A2.CompanyNumber)
