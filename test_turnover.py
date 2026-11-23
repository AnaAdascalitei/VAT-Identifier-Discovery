from turnover import parse_turnover, classify

IXBRL = """
<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><body>
<ix:header><ix:resources>
 <xbrli:context id="FY2024"><xbrli:entity><xbrli:identifier scheme="x">01234567</xbrli:identifier></xbrli:entity>
   <xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="FY2023"><xbrli:entity><xbrli:identifier scheme="x">01234567</xbrli:identifier></xbrli:entity>
   <xbrli:period><xbrli:startDate>2023-01-01</xbrli:startDate><xbrli:endDate>2023-12-31</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="FY2024_UK"><xbrli:entity><xbrli:identifier scheme="x">01234567</xbrli:identifier>
   <xbrli:segment><xbrldi:explicitMember dimension="geo">UK</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
   <xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
</ix:resources></ix:header>
<td><ix:nonFraction name="uk-core:TurnoverRevenue" contextRef="FY2023" unitRef="GBP" scale="3" decimals="-3" format="ixt:num-dot-decimal">9,100</ix:nonFraction></td>
<td><ix:nonFraction name="uk-core:TurnoverRevenue" contextRef="FY2024_UK" unitRef="GBP" scale="3" format="ixt:num-dot-decimal">5,000</ix:nonFraction></td>
<td><ix:nonFraction name="uk-core:TurnoverRevenue" contextRef="FY2024" unitRef="GBP" scale="3" decimals="-3" format="ixt:num-dot-decimal">12,345</ix:nonFraction></td>
<td><ix:nonFraction name="uk-core:ProfitLoss" contextRef="FY2024" unitRef="GBP" sign="-" format="ixt:num-dot-decimal">400</ix:nonFraction></td>
</body></html>
"""


def test_latest_whole_entity_period_with_scale():
    t = parse_turnover(IXBRL)
    assert t["turnover"] == 12_345_000          # scale=3, segment context ignored, latest year
    assert t["period_end"] == "2024-12-31"
    assert t["tag"] == "uk-core:TurnoverRevenue"


def test_no_turnover_tag():
    t = parse_turnover(IXBRL.replace("TurnoverRevenue", "Creditors"))
    assert t["turnover"] is None


def test_zero_dash():
    doc = IXBRL.replace(">12,345<", ">-<").replace('contextRef="FY2024" unitRef="GBP" scale="3" decimals',
                                                     'contextRef="FY2024" unitRef="GBP" format="ixt:fixed-zero" decimals')
    assert parse_turnover(doc)["turnover"] == 0


def test_ifrs_revenue_tag():
    doc = IXBRL.replace("uk-core:TurnoverRevenue", "ifrs-full:Revenue")
    assert parse_turnover(doc)["turnover"] == 12_345_000


def test_classify():
    assert classify(250_000, "43220 - Plumbing", "FULL") == "expected_registered"
    assert classify(5_000_000, "65120 - Non-life insurance", "FULL") == "uncertain_exempt_sector"
    assert classify(40_000, "62020 - IT consultancy", "MICRO ENTITY") == "below_threshold_voluntary_possible"
    assert classify(0, "64209 - Holding companies", "GROUP") == "holding_low_turnover"
    assert classify(None, "62020", "MICRO ENTITY") == "turnover_not_published"
    assert classify(None, "99999 - Dormant", "DORMANT") == "not_expected_dormant"


def test_other_namespace_prefix():
    doc = IXBRL.replace("xbrli:", "xbrl:")
    assert parse_turnover(doc)["turnover"] == 12_345_000


def test_consolidated_dimension_fallback():
    # Only a group/consolidated dimensional context carries turnover.
    doc = IXBRL.replace('contextRef="FY2024" unitRef="GBP" scale="3" decimals', 'contextRef="X" unitRef="GBP" scale="3" decimals')
    doc = doc.replace('contextRef="FY2023"', 'contextRef="X2"')
    doc = doc.replace('dimension="geo">UK', 'dimension="bus:GroupCompanyDataDimension">bus:ConsolidatedGroupDataMember')
    t = parse_turnover(doc)
    assert t["turnover"] == 5_000_000 and "consolidated" in t["tag"]


def test_only_annual_accounts_are_used():
    import turnover as tv

    class R:
        def __init__(self, code, js=None, text=""):
            self.status_code, self._js, self.text = code, js or {}, text
        def json(self):
            return self._js

    calls = []
    class FakeCH(tv.CH):
        def __init__(self):
            pass
        def get(self, url, **kw):
            calls.append(url)
            if "filing-history" in url:
                return R(200, {"items": [
                    {"type": "AA01", "date": "2026-05-01", "links": {"document_metadata": "M1"}},
                    {"type": "AA", "date": "2026-01-28", "links": {"document_metadata": "M2"}}]})
            if url == "M2":
                return R(200, {"resources": {"application/xhtml+xml": {}}})
            if url == "M2/content":
                return R(200, text=IXBRL)
            raise AssertionError(url)

    doc, note = FakeCH().latest_accounts_ixbrl("01234567")
    assert note == "2026-01-28" and "M1" not in calls
    assert tv.parse_turnover(doc)["turnover"] == 12_345_000


def test_untagged_turnover_in_income_statement():
    doc = ("<html><body><p>Strategic report: sales grew strongly.</p>"
           "<p>HUBBARD PRODUCTS LIMITED STATEMENT OF COMPREHENSIVE INCOME FOR THE YEAR ENDED 31 MARCH 2025</p>"
           "<table><tr><td></td><td>Notes</td><td>£</td><td>£</td></tr>"
           "<tr><td>Turnover</td><td>3</td><td>23,083,445</td><td>21,750,301</td></tr>"
           "<tr><td>Cost of sales</td><td></td><td><ix:nonFraction name='core:CostSales' contextRef='c'>18,921,245</ix:nonFraction></td></tr>"
           "</table></body></html>")
    t = parse_turnover(doc)
    assert t["turnover"] == 23_083_445 and "untagged" in t["tag"]


def test_tagged_value_wins_over_text():
    assert "untagged" not in parse_turnover(IXBRL)["tag"]


def test_holding_group_turnover_is_not_expected_registered():
    assert classify(15_822_036, "82990", "GROUP", "MEDIPLUS HOLDINGS LIMITED") == "group_turnover_holding_uncertain"
    assert classify(5_000_000, "25620", "FULL", "POLARSEAL HOLDINGS LIMITED") == "group_turnover_holding_uncertain"
    assert classify(5_000_000, "25620", "FULL", "ACME LTD", "uk-core:Turnover [consolidated dimension]") \
        == "group_turnover_holding_uncertain"
    assert classify(23_083_445, "28250", "FULL", "HUBBARD PRODUCTS LIMITED") == "expected_registered"


def test_missing_sic_is_uncertain():
    assert classify(16_227_298, "None Supplied", "FULL", "ZERO THREE CARE HOMES LLP") == "uncertain_no_sic"
    assert classify(16_227_298, "", "FULL", "X LLP") == "uncertain_no_sic"