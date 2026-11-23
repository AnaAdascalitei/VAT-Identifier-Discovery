import discover as d


def test_host_and_filter():
    assert d.host_of("https://www.subsea7.com/en/contact") == "subsea7.com"
    assert d.keep_host("subsea7.com")
    for bad in ("find-and-update.company-information.service.gov.uk", "endole.co.uk",
                "opencorporates.com", "uk.linkedin.com", "www.gov.uk"):
        assert not d.keep_host(d.host_of(bad)), bad


def test_parse_brave_drops_directories_and_dupes():
    payload = {"web": {"results": [
        {"url": "https://find-and-update.company-information.service.gov.uk/company/02798293"},
        {"url": "https://www.endole.co.uk/company/02798293"},
        {"url": "https://www.subsea7.com/legal"},
        {"url": "https://subsea7.com/contact"},
        {"url": "https://acme.co.uk/"},
        {"url": "https://other.co.uk/"},
    ]}}
    assert d.parse_brave(payload, top=2) == ["subsea7.com", "acme.co.uk"]
    assert d.parse_brave({}, top=3) == []


def test_parse_wikidata_pads_numbers():
    rows = [{"crn": {"value": "2798293"}, "site": {"value": "https://www.subsea7.com"}},
            {"crn": {"value": "sc123456"}, "site": {"value": "https://x.co.uk"}}]
    assert d.parse_wikidata(rows) == [("02798293", "https://www.subsea7.com"),
                                      ("SC123456", "https://x.co.uk")]


def test_main_with_no_results_writes_header(tmp_path, monkeypatch):
    import sys
    import pandas as pd
    pd.DataFrame({"CompanyNumber": ["00000001"], "CompanyName": ["X LTD"]}).to_csv(tmp_path / "s.csv", index=False)
    monkeypatch.setattr(d, "find_ch_property", lambda s: "P1")
    monkeypatch.setattr(d, "sparql", lambda s, q: [])
    monkeypatch.setattr(d.time, "sleep", lambda x: None)
    out = tmp_path / "c.csv"
    monkeypatch.setattr(sys, "argv", ["discover.py", str(tmp_path / "s.csv"), "--out", str(out),
                                      "--log", str(tmp_path / "l.jsonl")])
    d.main()
    assert out.read_text().strip() == "CompanyNumber,domain,source,rank"