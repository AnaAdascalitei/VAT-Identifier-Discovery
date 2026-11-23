# Can a UK company VAT dataset be built from the open web?

**Short answer: not from company websites alone.**

- On companies that are almost certainly VAT-registered, the company's own website showed the
  VAT number for **2 of 7**.
- My pipeline found and correctly attributed **2 of 7** (1 fully automatically, 1 with a
  hand-found domain). Every number it reported verified at HMRC: **0 false positives out of 3**.
- On small companies (~60% of all companies) it is usually **impossible to tell whether a
  company should have a number at all**: turnover was unpublished for 239 of 250 companies in
  sample B.
- 6 of 7 numbers exist somewhere and verify at HMRC, but 4 of those were visible only on
  commercial aggregators, whose source is not public.
- The bottleneck is not extraction. It is (1) the number not being on the website, (2) finding
  the right website, and (3) knowing whether "not found" means "not registered".

> All numbers below come from runs in this repo. Sample sizes are small; intervals are given.

---

## Part 1 — Research: what I tried, what came back

### What was not obvious at the start

- **"Not found" cannot be read without a turnover estimate.** I built one from Companies House
  accounts, and it only works for a minority of companies (see 1.8).
- **Large ≠ VAT-registered.** In my "large companies" sample, about half were holdings, group
  subsidiaries or vehicles trading outside the UK. They have no own website, or no own VAT number.
- **A valid VAT number can belong to the wrong company.** A group site, a sister company or a
  shared registered office produces real, HMRC-valid numbers attached to the wrong entity. This
  is the error the brief warns about, and I hit it three different ways.
- **The numbers exist, but not where expected.** Aggregators had 4 verified numbers that were
  not on the company's own site. Someone has a better source than crawling.

### 1.1 Verification: HMRC checker

- **Expected:** use the official API.
- **Found:** v2 requires an authenticated application on the HMRC Developer Hub, which I could
  not get in the time available.
- **Did instead:** automated the public web form (CSRF token from the first GET, then POST).
  - Bug: the field is `target`, not `vatNumber` (first POST returned 400).
  - Sanity: Google UK `765970776` → valid with matching name; `000000000` → invalid.
  - Burst test: 25 consecutive requests, ~0.29 s each, no CAPTCHA, block or 429.
    Burst only, not a sustained-load test.
- **Learned:** HMRC returns **name and address**. The name is the useful signal. The postcode is
  not: in 2 of 3 verified cases (Envision, Ichiban) the HMRC address differed from the
  Companies House registered office.

### 1.2 Population: Companies House bulk data

- Snapshot `BasicCompanyDataAsOneFile-2026-11-01`: **5,179,256 active companies**, not ~4.2M
  as in the brief (I counted status = "Active" only).
- Has name, number, registered address, SIC, accounts category. **No VAT number, no website.**

| Stratum (accounts category) | Companies | Share |
|---|---|---|
| micro | 1,676,273 | 32.4% |
| small | 1,438,205 | 27.8% |
| no accounts filed yet | 1,333,638 | 25.7% |
| dormant | 584,499 | 11.3% |
| large (full / group / medium) | 111,959 | 2.2% |
| subsidiary (exempt) | 34,682 | 0.7% |

### 1.3 Company websites: the legally required disclosure

- **Why it looked promising:** UK companies must show their **registered number** on their
  website (Trading Disclosures Regulations 2015), and VAT-registered businesses must show their
  **VAT number** (Electronic Commerce Regulations 2002). A footer carrying both is a perfect key,
  because it joins to Companies House on the number, not the name.
- **Exploratory test (biased, not a result):** 14 brands I picked (B2C, B2B SaaS,
  manufacturing). VAT found on **2 of 14** (Hotel Chocolat, Cult Beauty, both on /terms).
  0 on B2B / manufacturing. Gymshark: checked by hand that the number is really absent, not
  hidden by JavaScript.
- **On the real sample:** see 1.5–1.7. The disclosure law is widely ignored. The number was on
  the company's own site for 2 of 7 companies that are almost certainly registered.

### 1.4 Common Crawl (CDX index)

- **Expected:** read archived pages without hitting company servers.
- **Ran:** CDX lookups for known positive cases; byte-range WARC fetches.
  - Domain wildcards returned noise (third-party terms pages, UTM URLs).
  - Fetching without delays triggered HTTP 429; added throttling.
  - Sanity: commoncrawl.org itself → 43 captures.
- **Result:** homepages captured (Hotel Chocolat: 3), but the deeper /terms page that carries
  the VAT number was **not captured** for either positive case.
- **Conclusion, with its limit:** shown on **2 sites only**. That was enough to drop per-company
  CDX lookups on a laptop, but not to rule out Common Crawl. At scale the right shape is the
  Parquet URL index (filter `.uk` legal pages in bulk), which I did not run.

### 1.5 Finding the website: domain guessing (sample C, 100 companies)

- **Method:** guess `name.co.uk`, `name.com`, `name.uk` (with and without hyphens, generic words
  dropped). Accept a site only with evidence that it belongs to the company (1.7).

| Status | Companies |
|---|---|
| no site responded | 53 |
| site found but **not** provably this company's | 31 |
| site is the company's, no VAT on pages read | 13 |
| group / other company's site | 1 |
| VAT candidate | 2 (ValueLights, HMRC-confirmed; second not identified, the run was overwritten) |

- **Final C run (hand-found domains for the first 20 + Wikidata + guessing):** no site 48,
  not provably this company's 33, company's site without VAT 15, group site 1,
  **VAT candidates 3**: Steve Gregor (hand-found domain), ValueLights (guessed),
  Parking Control Management (UK) Ltd `812960238` (domain from **Wikidata**, company number on
  the same page). **HMRC: invalid.** The terms page also still shows a registered office the
  company has since left, so the page is stale. The number is rejected at the HMRC gate and never
  reaches the dataset (see 2.4).
- **Why it fails:** legal names ≠ trading domains (Hubbard Products → hubbardsystems.co.uk;
  Steve Gregor Plumbing → gregorheating.co.uk). Many guessed domains belong to someone else.
  One was parked and for sale (envisionenergy.com → domaineasy.com), and was correctly rejected.

### 1.6 Finding the website: other free sources

| Source | Expected | Result | Why it failed |
|---|---|---|---|
| **Wikidata** (company number → official website) | free, legal link | **2 of 100** in C, **0 of 250** in B | Covers well-known companies only. One of the two hits led to a VAT number (Parking Control Management). |
| **Google Custom Search API** | programmatic search | not usable | Closed to new customers (2026), shuts down 1 Jan 2027 |
| **Brave Search API** | ~2 queries/company | not used | Free tier removed in 2026; card + payment required. No budget. |
| **Manual search, AI-assisted** (20 companies) | upper bound for discovery | own site 7, group site 5, holding/no site 5, unclear 2 | See 1.7. Answers must be re-verified (1.10). |

### 1.7 Proving a site belongs to the company

Evidence levels, strongest first:

- `crn`: the company's **own** registered number is on the site → accepted.
- `other_crn`: a **different** company number is on the site (parent / group) → rejected,
  counted separately.
- `name_postcode`: all distinctive name tokens + registered postcode → accepted.
- `name_only` / `none` → rejected; any VAT found there is never reported.

**Bug found on real data, then fixed:** group sites passed `name_postcode`
(lr.org for *Lloyd's Register Central and South America Ltd*; orchid-ortho.com; stg.com).
Subsidiaries often share the parent's registered office, and group sites list subsidiary names.
Fix: a different company number on the site now overrides name + postcode. A regression test
reproduces the case.

**Run on the 20 annotated companies (manual domains first, then guesses):**

| Status | Companies |
|---|---|
| no site | 6 (≈ the holdings / no-own-site companies) |
| not provably this company's | 6 |
| company's site, no VAT found | 7 |
| VAT candidate | 1 (Steve Gregor) |

- 5 of the 12 hand-found domains **did not serve the crawler**, so a weaker guessed domain was
  used instead. From `fetch_log`, none was a robots.txt refusal:

| Site | What came back | Reading |
|---|---|---|
| eurizonsljcapital.com | **403** on the homepage (146-byte response) | bot blocked outright (WAF) |
| hubbardsystems.co.uk | homepage and /about **200**, but /contact and 3 other pages **403** | **path-level** blocking: the pages most likely to carry legal details are the ones refused |
| hmm21.com | read / connect **timeout** | blocked or unreachable from my network |
| ichibanuk.com | connect **timeout** | same |
| envision-group.com | **connection error** | same |

- I cannot fully separate "blocks bots" from "slow / geo-restricted" for the 3 timeouts without
  a second network. Either way, **5 of 12 (42%)** correct company sites were unusable to a
  polite crawler from a home connection.

### 1.8 Does "not found" mean "not registered"? Turnover from Companies House accounts

- **Idea:** registration is compulsory above **£90,000** taxable turnover. If accounts show
  turnover above that in a taxable activity, a missing number is **my** failure. Below it,
  voluntary registration is common, so it is not a negative.
- **Ran:** Companies House API → latest annual accounts (`AA`) → iXBRL → turnover.
- **Result on sample C (100):**

| Expectation | Companies |
|---|---|
| turnover not machine-readable | **82** |
| expected registered (≥ £90k, taxable SIC) | **7** |
| holding with group turnover | 4 |
| below threshold | 3 |
| exempt-heavy sector (finance, etc.) | 2 |
| holding, no turnover | 1 |
| no SIC supplied | 1 |

- **Why 82% is unusable** (each found on real filings):
  - 12 of the first 20 filed accounts as **PDF only**, with no iXBRL.
  - **Untagged turnover:** Hubbard's CCH-generated filing tags the balance sheet but leaves
    turnover (£23,083,445) as plain text. Added a text fallback, marked lower confidence.
  - Bug: `AA01` (change of accounting date) was read as accounts. Fixed: annual accounts only.
- **Classification traps, fixed:**
  - **Holdings** report the **group's** consolidated turnover (Mediplus, Polarseal, Frank Key,
    Tibbetts Holdings). The subsidiaries make the sales and hold the VAT number.
  - **LLPs don't supply a SIC code**, so exempt activity is invisible
    (Zero Three Care Homes LLP: care is VAT-exempt).
- **Limit:** micro and small companies (~60% of the population) generally file without a P&L,
  so this test cannot be applied to most of the customer's likely suppliers.

### 1.9 Commercial aggregators (CompanyCheck, Endole, credit-report sites)

- **Found by accident:** while checking misses by hand, aggregators had VAT numbers for
  Hubbard, Ichiban, Envision and Davis Track Hire. **All four verify at HMRC with matching names.**
- **None of the four was on the company's own site.** So aggregators have a source other than
  website crawling.
- **Automated access:** CompanyCheck refused my fetch; Endole's free page shows no VAT number
  (premium features are behind "Upgrade").
- **Decision:** use them **only as an evaluation reference** for recall, never as a product
  source (provenance unknown, anti-bot, UK database right). Contradicts "nobody sells it":
  they sell it inside paid reports.

### 1.10 Dead ends and traps, specifically

| Source / step | Expected | What actually happened |
|---|---|---|
| Domain guessing | find most company sites | 2 VAT candidates / 100; 31 sites that belong to someone else |
| Wikidata | free company → website link | 2 / 100 |
| Common Crawl CDX, per company | archived legal pages | legal pages not captured (2 of 2 tested) |
| EORI (GB + VAT + 000) | second route to the number | relationship confirmed, but no public directory; eorichecker.eu blocks automation |
| Turnover from accounts | classify every miss | readable for 18/100 large companies, fewer for small |
| AI search answers | faster annotation | mixed a UK holding with a dissolved Cypriot namesake (Tibbetts); offered a sister company's number (Mediplus Ltd for Mediplus Holdings) |
| Website number = valid number | yes | **Twice not.** Snowbird Foods (`927328019`) and Parking Control Management (`812960238`): both printed on the company's own site, both pass the checksum, both invalid at HMRC → stale numbers, not typos. PCM's page also shows an outdated registered office. |

---

## Part 2 — Proof of concept

### 2.1 Sample: how and why

- **Source:** the full Companies House active population (1.2). Seed 42; `sample.py` reproduces it.
- **Three samples, three questions:**

| Sample | Size | Drawn from | Answers |
|---|---|---|---|
| **A** random | 200 | all active companies | what the customer would actually see |
| **B** stratified | 250 (50 × 5 strata, dormant excluded, with weights) | non-dormant | where the method works and where it breaks |
| **C** large filers | 100 (FULL / GROUP / MEDIUM, dormant SIC excluded) | 111,959 | a population where "not found" is mostly a failure, so recall can be measured |

- **Not** drawn from companies I knew published their number. The 14-brand test (1.3) is
  labelled exploratory and excluded from every result.

### 2.2 Pipeline

```
sample.py      Companies House bulk → samples A/B/C
discover.py    company number → candidate websites (Wikidata; Brave if a key exists)
find_sites.py  candidates + guessed domains → crawl homepage + legal pages
               → evidence (crn / other_crn / name_postcode / ...) → VAT candidates
vat_extract.py VAT, EORI and company-number regexes + HMRC mod-97 checksum (both schemes)
turnover.py    Companies House API → iXBRL accounts → turnover → "should this company be registered?"
HMRC check     every reported number: valid? AND registered name matches the company?
```

- 46 unit tests, including the real cases that broke it (group site, parked domain, untagged
  turnover, AA01, LLP without SIC).
- Every HTTP request and search result is logged (`fetch_log_*.csv`, `discover_log.jsonl`).

### 2.3 Results on sample C

**Reference set:** the 7 companies whose accounts show turnover ≥ £90k in a taxable activity
(not holdings, SIC present). For each I searched by hand and verified at HMRC.

| Company | VAT number | HMRC valid + name matches | On own website | Found by pipeline |
|---|---|---|---|---|
| Steve Gregor Plumbing and Heating | 543162171 | yes | yes (footer, with company no.) | **yes** (hand-found domain) |
| Hubbard Products | 905112567 | yes | no | no |
| Ichiban UK | 176707773 | yes | no | no |
| Envision Energy International UK | 420211272 | yes | no | no |
| ValueLights | 995942647 | yes | **yes** (/terms-and-conditions) | **yes** (guessed domain, fully automatic) |
| Davis Track Hire | 991064409 | yes | no | no |
| Merit Office Installations | not found anywhere | — | — | no |

| Metric | Value | 95% interval |
|---|---|---|
| Recall of the pipeline (found / expected registered) | **2 / 7 = 29%** | 8% – 64% |
| ...fully automatic (no hand-found domain) | 1 / 7 = 14% | 3% – 51% |
| Ceiling for any website crawler (number on own site / expected) | **2 / 7 = 29%** | 8% – 64% |
| Numbers that exist and verify (any source) | 6 / 7 | 49% – 97% |
| False positives in the final dataset (C + B) | **0 / 3** | 0% – 56% |
| Site-published candidates rejected by HMRC as stale | 1 / 4 | — |

### 2.4 False-positive rate: how it is measured

- **Definition:** a reported number is a false positive if HMRC says it is invalid, **or** the
  HMRC registered name is not this company.
- **Measured on:** every number the pipeline reports, on C and B. No sub-sampling.
- **Two stages, reported separately:**

| Stage | Result |
|---|---|
| Candidates extracted from verified company sites (C + B) | 4 |
| ...rejected by HMRC as invalid (stale number) | **1 / 4** (Parking Control Management) |
| **Numbers in the final dataset** | 3: Steve Gregor (C), ValueLights (C), Future Forestry (B, small) |
| ...false positives (invalid or another company's name) | **0 / 3** (95% interval 0–56%) |

- So **HMRC verification is not optional**: 1 in 4 numbers the company itself publishes was
  stale. Without the gate, the dataset would have had a 25% error rate on this (small) sample.
- 3 numbers are too few for a precise rate, and I say so.
- **Name matching needs normalisation:** HMRC returned "STEVE GREGOR PLUMBING **&** HEATING
  **LTD**" for "...PLUMBING **AND** HEATING **LIMITED**". An exact string match would have
  called a correct number wrong.
- **False positives prevented** (real cases, not hypothetical):
  - **Yolo (Ponteland) Ltd (sample B):** the guessed site yolo.co.uk carried VAT `455078773`,
    but only the name matched (`name_only`), so it was rejected. The company's real number,
    HMRC-confirmed, is **`213818814`**, and its real site is yolo.uk.com. Accepting name-only
    evidence would have produced exactly the invisible wrong-company error the brief describes.
  - Mediplus Holdings: a valid number exists, but it belongs to sister company Mediplus Ltd → not attached.
  - Group sites (lr.org, orchid-ortho.com, stg.com) → now rejected by `other_crn`.
  - 31 guessed sites belonging to other companies → rejected, VAT never reported.
  - Parked domain envisionenergy.com → rejected.

### Evidence: HMRC checks (screenshots)

Every number in this report was checked by hand on gov.uk/check-uk-vat-number. Screenshots
are in [`evidence/hmrc/`](evidence/hmrc/).

| VAT number | Company | Role in the report | HMRC result | Screenshot |
|---|---|---|---|---|
| 543162171 | Steve Gregor Plumbing and Heating | found by pipeline (C) | valid, name matches | [link](evidence/hmrc/543162171_steve_gregor_hmrc.png) |
| 995942647 | ValueLights | found by pipeline (C) | valid, name matches | [HMRC](evidence/hmrc/995942647_valuelights_hmrc.png), [website](evidence/hmrc/995942647_valuelights_website_terms.png) |
| 743241553 | Future Forestry | found by pipeline (B, small) | valid, name matches | [link](evidence/hmrc/743241553_future_forestry_hmrc.png) |
| 905112567 | Hubbard Products | reference, missed | valid, name matches | [link](evidence/hmrc/905112567_hubbard_products_hmrc.png) |
| 176707773 | Ichiban UK | reference, missed | valid, name matches | [link](evidence/hmrc/176707773_ichiban_uk_hmrc.png) |
| 420211272 | Envision Energy International UK | reference, missed | valid, name matches | [link](evidence/hmrc/420211272_envision_energy_hmrc.png) |
| 991064409 | Davis Track Hire | reference, missed | valid, name matches | [link](evidence/hmrc/991064409_davis_track_hire_aggregator_and_hmrc.png) |
| 213818814 | Yolo (Ponteland) | proves the rejected number (455078773) was wrong | valid, name matches | [link](evidence/hmrc/213818814_yolo_ponteland_hmrc.png) |
| 812960238 | Parking Control Management (UK) | candidate from the company's own site | **invalid** | checked by hand, no screenshot kept |

### 2.5 Results on sample B

- **Sampling flaw, found and declared:** B's `large` stratum is **identical to 50 companies of
  C**. Both were drawn from the same pool with the same seed. B therefore adds information only
  on its other 4 strata (**200 companies**); large companies are reported from C. In production
  each sample would use its own seed or be drawn disjoint.

| B (all 250) | Companies |
|---|---|
| no site | 131 |
| site not provably this company's | 91 |
| company's site, no VAT | 21 |
| group / other company's site | 3 |
| VAT candidate | 3 (Gregor and ValueLights from the overlapping large stratum; **Future Forestry**, small) |
| VAT on a rejected site | 1 (Yolo: correctly rejected, see 2.4) |

- **Wikidata: 0 of 250** companies had a website.
- **On the 200 new companies: 1 number found and HMRC-confirmed** (Future Forestry, small, Scotland).
- **Turnover not published: 239 of 250.** In the micro, small, no-accounts and subsidiary
  strata **no company** could be classified as "expected registered". For most of the
  customer's likely suppliers, a miss cannot be told apart from a true negative.
**Per stratum** (the `large` row duplicates C and is shown only for completeness):

| Stratum | No site | Site not provably theirs | Company's site, no VAT | Group site | VAT on rejected site | **VAT candidate** |
|---|---|---|---|---|---|---|
| micro | 24 | 24 | 2 | 0 | 0 | **0** |
| small | 25 | 22 | 2 | 0 | 0 | **1** |
| no_accounts | 31 | 15 | 2 | 2 | 0 | **0** |
| subsidiary | 32 | 10 | 7 | 0 | 1 | **0** |
| *large (= C)* | *19* | *20* | *8* | *1* | *0* | *2* |
| **New strata, total (200)** | **112** | **71** | **13** | **2** | **1** | **1** |

- On the 200 new companies, a site provably belonging to the company was found for only
  **14 (7%)**. One of those 14 showed a VAT number.
- **The binding constraint for small companies is finding the site, not reading it**: 56% had
  no site at all, and 36% had a site that could not be tied to them (mostly namesakes).
- `subsidiary` has the most "company's site, no VAT" (7). These are typically group or brand
  sites where the subsidiary is named but its own number is not shown.

### 2.6 What these numbers do not capture

- **Small n.** 7 reference companies; intervals are wide. The direction (most numbers are not
  on websites) is clear; the exact rate is not.
- **The reference is biased toward findable numbers.** The 6 verified numbers came from sources
  I could reach. A registered company whose number is nowhere public (Merit) cannot appear in
  the reference, so the true ceiling may be lower.
- **C is not the customer's supplier base.** Large filers are 2% of companies. The customer's
  suppliers are mostly small; B and A address that.
- **"Expected registered" covers only 7% of C** (82% have no readable turnover), so recall is
  measured on a minority even within C.
- **VAT groups:** a subsidiary in a group legitimately invoices under the group's number. My
  rules treat that as "not this company's number", so they undercount for groups.
- **Website disclosure ≠ active registration** (Snowbird). Every number is re-checked at HMRC;
  a stale number on a site counts as not found.
- **Crawler limits:** no JavaScript rendering, homepage + up to 5 linked legal pages, same host
  only, some sites did not respond.

---

## Part 3 — With real resources

### 3.1 What actually limits coverage

| Limit | Fixed by money? | Evidence |
|---|---|---|
| Number not on the website | **No** | 2 / 7 own-site ceiling |
| Finding the right website | Yes (search API + annotation) | guessing 2/100; manual 7 own sites / 20 |
| Crawler misses (JS, blocks) | Yes (headless, proxies) | 5 of 12 correct sites refused / timed out |
| Knowing who should be registered | Partly (OCR on PDF accounts) | 82 / 100 unreadable |
| Holdings / VAT groups | No (not public) | 4 holdings + 5 group sites in 20 |

**Implication:** my pipeline already reached the own-website ceiling on this small sample
(2/7 found, 2/7 on own sites), with help on one domain. A bigger crawler makes that automatic
and robust; it does **not** move recall past ~30%.
Beyond it you need a **different source**, not more crawling.

### 3.2 What I would build, in order

1. **Fix the crawler to reach the ceiling (weeks 1–4).**
   - Search API for domain discovery: 2 queries/company (company number in quotes; name + postcode).
   - Headless rendering only where the static fetch finds no legal links.
   - Crawl the same registered domain (subdomains), and Impressum-style pages (`/legal`, `/terms`, `/imprint`).
   - Residential proxies only for sites that block datacentre IPs.
2. **Find out where aggregators get their numbers (weeks 1–2, in parallel).**
   - Ask for a quote / data licence from 2–3 vendors (credit-reference agencies, Endole-type sites).
   - Compare price per *verified* number with the crawl cost below. Licensed data plus our own
     HMRC verification is likely cheaper per found number than crawling.
3. **The customer's own invoices (highest value for this customer).**
   - Every VAT invoice they receive must carry the supplier's VAT number.
   - OCR / e-invoice parsing on their AP documents, then verify at HMRC, then attach to the supplier.
   - This covers exactly the suppliers they pay, which is the population they care about.
4. **Entity resolution on Companies House for deduplication, independent of VAT.**
   - Match every supplier record (name + address) to a company number. Two records with the
     same company number are the same supplier, with or without a VAT number.
5. **OCR of PDF accounts** to extend the "should be registered" test beyond 18% of large companies.

### 3.3 Rough cost per company (to argue with)

Assumptions are explicit; change them and the result moves.

| Item | Unit cost | Per company |
|---|---|---|
| Search API, 2 queries | ~$5 / 1,000 queries | ~$0.010 |
| Static crawl, ~6 pages | compute + bandwidth | ~$0.001 |
| Headless render, needed for ~30% | ~$0.01 / site | ~$0.003 |
| Residential proxy, ~10 MB for ~20% of sites | a few $ / GB | ~$0.01 |
| HMRC verification | free (API), rate-limited | $0 |
| Audit annotation, 1% of records, ~2 min each | ~$20 / h | ~$0.007 |
| **Discovery, per company processed** | | **≈ $0.03** |

- **Per number found, not per company:** if ~30% of active companies are VAT-registered (an
  assumption; ~2.18M registrations include sole traders) and websites reveal ~30% of those, then
  yield ≈ 9% → **≈ $0.33 per verified number**.
- Run on non-dormant companies only (~4.6M): **≈ $140k** discovery spend for **≈ 400k numbers**
  under these assumptions. That is the figure to beat with a data licence.

### 3.4 What breaks first

1. **HMRC throughput.** At ~3 requests/second per application, one pass over 1.5M numbers takes
   ~6 days. Monthly re-verification is feasible only with a raised limit, agreed with HMRC, or by
   prioritising the customer's active suppliers.
2. **Anti-bot blocking.** 5 of 12 hand-found company sites refused or timed out a polite
   crawler (2 × 403, one of them only on /contact-type pages; 3 × timeout / connection error).
   At scale this is the first silent coverage loss, and it hits exactly the legal pages.
3. **Wrong-entity numbers through groups.** Precision falls quietly as coverage rises, because
   the easy remaining numbers are group numbers. Needs its own tier and audit.
4. **Domain discovery quality.** Search results include directories and namesakes; the evidence
   rules are what keep precision, and they need maintenance.

### 3.5 What I would monitor in production

- **Per source:** yield, HMRC pass rate, name-match rate, week over week.
- **Per stratum:** coverage of `expected_registered` companies (the recall proxy).
- **Rejections:** counts of `other_crn`, `name_only` and parked domains. A spike means search
  quality changed.
- **Staleness:** share of records whose last HMRC check is older than the SLA.
- **Audit:** monthly random sample per stratum, precision with an interval.
- **Customer feedback:** invoice-vs-record mismatches, the best labelled error signal there is.

---

*Debate topics and Beyond the UK: see below.*

---

# Debate topics

## 1. Brute-forcing HMRC with the checksum

- **The math:** I sampled random 9-digit strings and ~2.1% pass the checksum, so there are
  about **21M candidates**. At ~3 req/s that is **~81 days**.
- **What you'd get:** every registration, with name + address.
- **Why I think it's a bad idea:**
  - **It doesn't solve the problem:** no company number comes back, so you are matching on
    name again.
  - **Personal data:** sole traders' names and home addresses.
  - **Misuse** of a verification service; it risks losing API access the product needs.
- **What I used the checksum for instead:** a free local filter, and a way to tell a typo from a stale
  number (Snowbird, PCM).

## 2. Keeping it current

- **Verification vs discovery:**
  - re-check every number at HMRC on a schedule → **cheap, continuous**;
  - re-run discovery only on events (new company, new accounts > £90k, number turns invalid)
    → **expensive, triggered**.
- **Why:** VAT (de)registration triggers nothing at Companies House.
- **Evidence from my sample:** 2 numbers that companies publish on their sites right now are
  already invalid at HMRC (Snowbird, PCM).

## 3. Knowing it's wrong, with no complete reference

- **HMRC on every record:** valid **and** name matches (normalised: "&" = "AND", "LTD" = "LIMITED").
- **Monthly random audit per stratum** → precision with an interval.
- **Red flags:**
  - one number on many companies (VAT group or error);
  - an expected-registered company with no number (recall gap).
- **Drift:** yield per source drops suddenly → the source broke, not the companies.
- **Best ground truth:** the customer's own invoices, compared with our record.

## 4. Sources: product vs not

| Would use in the product | Would not use |
|---|---|
| Company's own website (legal disclosure, URL kept) | **Aggregators** (CompanyCheck, Endole): unknown source, anti-bot, database right |
| Companies House (open data) | **AI search answers**: mixed up entities (Tibbetts / Cyprus, Mediplus Ltd vs Holdings) |
| HMRC **API** | HMRC web form scraping |
| Wikidata (CC0) | Checksum enumeration |

- I did use aggregators, but only as a reference to measure recall, not as a source.

---

# Beyond the UK

## UK vs Germany

| | UK | Germany |
|---|---|---|
| **Discovery** | hard: number on own site for 2/7 | easier: the mandatory *Impressum* page must show the USt-IdNr. |
| **Verification** | easy: HMRC returns name + address | hard: VIES returns **no name** for DE |
| **"No number" means** | probably not registered | **not** "not registered": USt-IdNr. is issued **only on request** |
| **Entity proof** | company no. + VAT in footer | HRB + USt-IdNr. on the same Impressum |

- **Would my pipeline survive?** I think the extraction part would; the verification part
  would not.
- **The risk:** wrong-company numbers can't be caught by the verifier, so the audit carries
  all of it.

## Where the number is barely discovered

- **France:** VAT = FR + key + SIREN, key = (12 + 3 × (SIREN mod 97)) mod 97.
  **Computed, not found.** VIES only says active / not.
- **Belgium:** VAT = BE + enterprise number. Same idea.
- **What this means for prioritising:** I would start with France / Belgium, then Germany,
  and leave the UK for last.
  - I would rank markets by:
    1. Is the number derivable from a public ID?
    2. Does the verifier return the name?
    3. Is there a standard disclosure page?

## Hard countries

| Country | Discovery | Verification |
|---|---|---|
| UK | hard | easy |
| Germany | easier | hard |
| Spain | hard | hard (no name from VIES) |
| France / Belgium | trivial | easy |

---

### Sources
- HMRC rate limit: https://apis.io/rate-limits/hmrc/hmrc-rate-limits/
- Impressum, USt-IdNr. "sofern vorhanden": https://www.ihk.de/dresden/hauptnavigation/recht-steuern/merkblaetter-von-a-bis-z/impressum--6697212
- USt-IdNr. only on request: https://www.it-recht-kanzlei.de/duerfen-kleinunternehmer-umsatzsteueridentifikationsnummer-beantragen.html
- VIES, no name for DE/ES: https://www.vatstack.com/articles/what-is-vies
- French key: https://l-expert-comptable.com/calculateurs/trouver-le-numero-de-tva-intracommunautaire-partir-de-votre-numero-de-siren.html
- Belgian VAT = enterprise number: https://lookuptax.com/docs/tax-identification-number/belgium-tax-id-guide
