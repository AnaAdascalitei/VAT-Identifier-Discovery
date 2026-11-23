"""Tests for find_sites.py against a local fake web server (no internet needed)."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import find_sites as fs

REAL_GUESS = fs.guess_domains      # saved before the autouse fixture patches it
SPOTON = "930929315"
GOOGLE = "765970776"

PAGES = {
    # 1. Correct site: company number + VAT in the footer of the contact page.
    "/acme/": '<html><body><h1>Acme Widgets</h1><a href="/acme/contact-us">Contact</a>'
              '<a href="/acme/blog">Blog</a></body></html>',
    "/acme/contact-us": "<footer>Acme Widgets Ltd. Registered in England and Wales, "
                        f"company number 06139437. VAT No: GB {SPOTON}</footer>",
    # 2. No company number, but full name + registered postcode + VAT.
    "/bolt/": "<p>Bolt Fixings Limited, 2 Mill Lane, Leeds LS1 1AA</p>"
              f"<footer>VAT Reg: {GOOGLE}</footer>",
    # 3. Wrong company's site (similar name), with a perfectly valid VAT number.
    "/wrong/": "<p>Crown Paints Direct - the paint people</p>"
               f"<footer>VAT number GB{SPOTON}</footer>",
    # 4. Right company, no VAT shown.
    "/novat/": "<p>Delta Joinery Ltd, company no 11223344</p>",
    # 5. Two VAT numbers; only one sits next to this company's own number.
    "/two/": '<a href="/two/legal">Legal</a><p>Partner: Foo Ltd VAT ' + GOOGLE + "</p>",
    "/two/legal": f"Echo Group Ltd. Company No 22334455. VAT {SPOTON}",
    # 7. Parent group's site: shows a DIFFERENT company number (Subsea 7 case).
    "/group/": f"<p>Seven Group plc, company number 01111111. VAT {SPOTON}</p>",
    # 6. Valid-looking but bad-checksum VAT only.
    "/badcs/": "Fox Ltd company number 33445566. VAT No GB123456789",
}


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def base():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def no_guessing(monkeypatch):
    monkeypatch.setattr(fs, "guess_domains", lambda name: [])   # never touch the real internet
    monkeypatch.setattr(fs, "DELAY", 0)


def run(base, tmp_path, crn, name, postcode, path):
    f = fs.Fetcher(str(tmp_path / "log.csv"))
    row = {"CompanyNumber": crn, "CompanyName": name, "RegAddress.PostCode": postcode}
    manual = {crn: [(base + path, "manual")]} if path else {}
    return fs.process(f, row, manual)


def test_crn_evidence_and_link_following(base, tmp_path):
    r = run(base, tmp_path, "06139437", "ACME WIDGETS LTD", "TQ1 4BX", "/acme/")
    assert r["site_evidence"] == "crn"
    assert r["status"] == "vat_candidate" and r["vat"] == SPOTON
    assert r["vat_url"].endswith("/acme/contact-us")
    assert r["pages_fetched"] == 2           # homepage + contact; /blog not followed


def test_name_postcode_evidence(base, tmp_path):
    r = run(base, tmp_path, "44556677", "BOLT FIXINGS LIMITED", "LS1 1AA", "/bolt/")
    assert r["site_evidence"] == "name_postcode"
    assert r["status"] == "vat_candidate" and r["vat"] == GOOGLE


def test_wrong_site_is_never_accepted(base, tmp_path):
    r = run(base, tmp_path, "55667788", "CROWN JOINERY LTD", "M1 1AA", "/wrong/")
    assert r["site_evidence"] == "none"
    assert r["status"] == "site_unverified_has_vat"


def test_site_without_vat(base, tmp_path):
    r = run(base, tmp_path, "11223344", "DELTA JOINERY LTD", "B1 1AA", "/novat/")
    assert r["status"] == "site_ok_no_vat" and r["vat"] == ""


def test_two_vats_pick_the_one_next_to_own_crn(base, tmp_path):
    r = run(base, tmp_path, "22334455", "ECHO GROUP LTD", "", "/two/")
    assert r["status"] == "vat_candidate" and r["vat"] == SPOTON


def test_bad_checksum_only(base, tmp_path):
    r = run(base, tmp_path, "33445566", "FOX LTD", "", "/badcs/")
    assert r["status"] == "vat_bad_checksum_only"


def test_no_site(base, tmp_path):
    r = run(base, tmp_path, "99887766", "NOWHERE LTD", "", None)
    assert r["status"] == "no_site_found"


def test_fetch_log_written(base, tmp_path):
    run(base, tmp_path, "06139437", "ACME WIDGETS LTD", "", "/acme/")
    log = (tmp_path / "log.csv").read_text()
    assert "/acme/contact-us,200" in log


def test_guess_domains():
    d = REAL_GUESS("J. SMITH BUILDING SERVICES (UK) LIMITED")
    assert "jsmithbuildingservices.co.uk" in d
    assert "j-smith-building-services.com" in d
    assert "jsmithbuilding.co.uk" in d          # generic words dropped
    assert all("limited" not in x and "uk" not in x.split(".")[0] for x in d)


def test_group_site_detected(base, tmp_path):
    r = run(base, tmp_path, "02798293", "SEVEN WEST AFRICA CONTRACTING LIMITED", "SM2 5BN", "/group/")
    assert r["site_evidence"] == "other_crn"
    assert r["status"] == "group_or_other_company_site" and r["vat"] == SPOTON
    assert r["domain_source"] == "manual"


def test_generic_words_not_required(base, tmp_path):
    # Registered as "...SERVICES LTD" but the site just says "Bolt Fixings".
    r = run(base, tmp_path, "44556677", "BOLT FIXINGS SERVICES LIMITED", "LS1 1AA", "/bolt/")
    assert r["site_evidence"] == "name_postcode"


def test_directory_sites_skipped(tmp_path):
    f = fs.Fetcher(str(tmp_path / "log.csv"))
    row = {"CompanyNumber": "02798293", "CompanyName": "X LTD", "RegAddress.PostCode": ""}
    r = fs.process(f, row, {"02798293": [("find-and-update.company-information.service.gov.uk", "brave_crn"),
                                          ("endole.co.uk", "brave_crn")]})
    assert r["status"] == "no_site_found" and r["domains_tried"] == 0


def test_load_manual_merges_files(tmp_path):
    a = tmp_path / "m.csv"; a.write_text("CompanyNumber,domain,note\n2798293,subsea7.com,group\n")
    b = tmp_path / "c.csv"; b.write_text("CompanyNumber,domain,source,rank\n02798293,x.co.uk,brave_crn,1\n")
    m = fs.load_manual(str(a), str(b))
    assert m == {"02798293": [("subsea7.com", "manual"), ("x.co.uk", "brave_crn")]}