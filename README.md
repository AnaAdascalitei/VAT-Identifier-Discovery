# UK company VAT numbers from the open web: feasibility study

## Summary

- **Company websites are not enough.** Of 7 companies that almost certainly must be
  VAT-registered, only **2** show their VAT number on their own site.
- **My pipeline found 2 of 7** (one fully automatic, one with a domain I found by hand).
- **0 false positives in the final data (0/3).** But **1 of 4** numbers that companies publish on
  their own site was already invalid at HMRC, so the HMRC check is mandatory.
- **Most "not found" results can't be interpreted.** For 239 of 250 smaller companies, turnover
  is not published, so I cannot tell "not registered" from "I missed it".
- **The numbers exist somewhere else:** 4 HMRC-valid numbers were on commercial aggregators but
  not on the company's site.

Samples are small; I give intervals where it matters.

---

## Part 1: Research

### What I tried, in order

**1. Verifier first (HMRC)**
- Wanted: the official API. It needs an authenticated Developer Hub application, which I could
  not get in time.
- Did: automated the public web form.
  - First POST gave 400: the field is `target`, not `vatNumber`.
  - Tested 765970776 (Google UK) → valid; 000000000 → invalid.
  - 25 requests in a row, ~0.29 s each, no blocking. Only a burst test.
- Learned: HMRC returns the **name** (useful) and an address. The address often differs from
  Companies House (2 of 3 cases), so I match on name only.

**2. Population (Companies House bulk)**
- `BasicCompanyDataAsOneFile-2026-11-01`: **5,179,256 active companies** (not 4.2M as in the
  brief; I counted status "Active" only).
- No VAT numbers, no websites.

**3. Company websites (legal disclosure)**
- Why it looked good: companies must show their **company number** on their website (2015
  regulations), and VAT-registered businesses their **VAT number** (2002 regulations). Both on
  one page = an exact join to Companies House.
- First test, 14 brands I picked myself (biased, exploration only): VAT found on **2/14**.
  Gymshark: checked by hand that the number really isn't on the site.

**4. Common Crawl (CDX index)**
- Expected: archived legal pages without hitting company servers.
- Got: homepages were archived, but the /terms pages with the VAT number were **not**, for both
  test cases. I also hit 429 until I added delays.
- Decision: dropped per-company lookups. Only 2 sites tested, so I don't rule out Common Crawl
  in bulk (Parquet URL index); I just couldn't run that on a laptop.

**5. EORI**
- EORI = GB + VAT number + 000, so it is a second route.
- Dropped: no public directory, and eorichecker.eu blocks automated access.

**6. Finding the company's website**

| Method | Result on sample C (100) | Decision |
|---|---|---|
| Guess domain from name | 2 VAT candidates; 31 sites belonged to other companies | keep, but needs strict checks |
| Wikidata (company no. → website) | 2/100 (B: 0/250) | keep, tiny coverage |
| Google search API | closed to new customers | not possible |
| Brave search API | paid only (2026) | no budget |
| Manual, AI-assisted (20 companies) | own site 7, group site 5, no site 5, unclear 2 | used as an upper bound |

**7. Proving the site belongs to the company**
- Rules: accept only if the company's **own number** is on the site, or **name + registered
  postcode**. If a **different** company number is on the site, it is a group site → reject.
- **Bug found on real data:** group sites (lr.org, orchid-ortho.com, stg.com) passed
  "name + postcode", because subsidiaries share the parent's office. Fixed: a different company
  number now wins.
- Also caught: a parked domain (envisionenergy.com → domaineasy.com), rejected.
- **5 of 12** correct company sites didn't serve my crawler: 2 × HTTP 403 (one only on /contact),
  3 × timeouts.

**8. Is "not found" a miss or a true negative?**
- Idea: registration is compulsory above **£90k** turnover. If accounts show more than that in
  a taxable activity, a missing number is my failure.
- Did: Companies House API → latest accounts (iXBRL) → turnover.
- Problems found, and what I did:
  - **82/100** large companies: turnover not machine-readable (mostly PDF filings).
  - Hubbard: turnover written in the document but **not tagged** → added a text fallback.
  - `AA01` filings read as accounts by mistake → fixed, annual accounts only.
  - **Holdings** report group turnover → put in a separate class.
  - **LLPs have no SIC code**, so exempt activity is invisible (care homes) → separate class.
- Result on C: **7** companies "expected registered". On B (smaller companies): **0** outside
  the large stratum (239/250 not published).

**9. Aggregators (found by accident)**
- While checking misses by hand: CompanyCheck / Endole-type sites had numbers for Hubbard,
  Ichiban, Envision and Davis Track Hire. **All valid at HMRC; none on the company's own site.**
- CompanyCheck blocked my fetch; Endole's free page doesn't show VAT.
- Decision: use them **only as a reference** to measure recall, not as a source.

### Dead ends and traps

| What | Expected | What happened |
|---|---|---|
| Domain guessing | most sites | 31/100 guessed sites were someone else's |
| Wikidata | free links | 2/100, 0/250 |
| Common Crawl per company | legal pages | not archived (2/2) |
| Turnover from accounts | classify every miss | readable for 18/100 large, ~0 small |
| AI search answers | faster annotation | mixed a UK holding with a Cypriot namesake (Tibbetts); offered a sister company's number (Mediplus) |
| Number on own site = valid | yes | **no, twice:** Snowbird `927328019` and PCM `812960238` pass the checksum but are invalid at HMRC (stale) |

---

## Part 2: Proof of concept

### Data used (not in the repo)

- `BasicCompanyDataAsOneFile-2026-11-01.csv` (Companies House Free Company Data Product,
  1 Nov 2026).
- Not on GitHub: the file is over GitHub's 100 MB limit.
- Reproduce: download that snapshot and run `python sample.py BasicCompanyDataAsOneFile-2026-11-01.csv --seed 42`.
  The drawn samples are committed in `samples/`.

### Sample

| Sample | Size | From | Question it answers |
|---|---|---|---|
| A, random | 200 | all active | what the customer would see |
| B, stratified | 250 (5 × 50, no dormant) | non-dormant | where the method works |
| C, large filers | 100 | FULL / GROUP / MEDIUM accounts | measure recall where "not found" is mostly a miss |

- Not companies I knew. The 14-brand test is excluded from all results.
- **Mistake I found:** B's `large` stratum is the same 50 companies as part of C (same pool,
  same seed). So B adds only **200 new companies**; I report large companies from C.

### Pipeline

```
sample.py      Companies House CSV -> samples A / B / C
discover.py    company number -> candidate sites (Wikidata)
find_sites.py  guess + candidates -> crawl home + legal pages -> prove ownership -> VAT candidates
vat_extract.py regexes for VAT / EORI / company number + HMRC mod-97 checksum
turnover.py    Companies House accounts -> turnover -> "should this company be registered?"
HMRC check     every number: valid AND name matches the company
```

47 tests, several built from cases that broke on real data.

### Results on sample C: the 7 companies that should be registered

Screenshots of every HMRC check are in the repo root.

| Company | VAT | HMRC | On own site | Pipeline | Evidence |
|---|---|---|---|---|---|
| Steve Gregor Plumbing and Heating | 543162171 | valid | yes | **found** (hand-found domain) | `543162171_steve_gregor_hmrc.png` |
| ValueLights | 995942647 | valid | yes | **found** (automatic) | `995942647_valuelights_hmrc.png`, `995942647_valuelights_website_terms.png` |
| Hubbard Products | 905112567 | valid | no | missed | `905112567_hubbard_products_hmrc.png` |
| Ichiban UK | 176707773 | valid | no | missed | `176707773_ichiban_uk_hmrc.png` |
| Envision Energy International UK | 420211272 | valid | no | missed | `420211272_envision_energy_hmrc.png` |
| Davis Track Hire | 991064409 | valid | no | missed | `991064409_davis_track_hire_aggregator_and_hmrc.png` |
| Merit Office Installations | not found anywhere | n/a | no | missed | n/a |

| Metric | Value | 95% interval |
|---|---|---|
| Recall (found / should be registered) | **2/7 = 29%** | 8–64% |
| Fully automatic | 1/7 = 14% | 3–51% |
| Number on own website (ceiling for any crawler) | 2/7 = 29% | 8–64% |

### False positives

- **Definition:** invalid at HMRC, or HMRC's name is not this company.
- **Measured on:** every number the pipeline produced (C + B).

| Stage | Result |
|---|---|
| Candidates from verified company sites | 4 |
| Rejected by HMRC (stale) | **1/4** (Parking Control Management, `812960238`) |
| **Final numbers** | 3: Gregor, ValueLights, Future Forestry (`743241553_future_forestry_hmrc.png`) |
| **False positives** | **0/3** (interval 0–56%; too few to be precise) |

- Name matching must be normalised: HMRC writes "& ... LTD", Companies House "AND ... LIMITED".
- **Wrong numbers I avoided:**
  - **Yolo (Ponteland):** a guessed site (yolo.co.uk) had `455078773`, but only the name
    matched → rejected. The real number is `213818814` (`213818814_yolo_ponteland_hmrc.png`).
  - **Mediplus Holdings:** a valid number exists, but it belongs to sister company Mediplus Ltd.
  - Group sites, a parked domain, and 31 sites of other companies.

### Results on sample B (200 new companies)

| Stratum | No site | Site not theirs | Own site, no VAT | VAT found |
|---|---|---|---|---|
| micro | 24 | 24 | 2 | 0 |
| small | 25 | 22 | 2 | **1** (Future Forestry) |
| no accounts yet | 31 | 15 | 2 | 0 |
| subsidiary | 32 | 10 | 7 | 0 |

- Only **14/200 (7%)** had a site I could prove was theirs. For small companies the problem is
  **finding the site**, not reading it.

### What my numbers don't capture

- **Small n:** 7 reference companies, 3 final numbers. The direction is clear; exact rates are not.
- **The reference is biased** toward numbers I could find. Companies whose number is nowhere
  public (Merit) are invisible.
- **C is 2% of companies.** The customer's suppliers are mostly small, where I can't measure
  recall at all.
- **VAT groups:** a subsidiary may legally use the group's number. I count that as "not this
  company's", so I undercount groups.
- **Crawler limits:** no JavaScript, homepage + 5 legal pages, one host only.

---

## Part 3: With real resources

### What limits coverage, and whether money fixes it

| Limit | Money fixes it? | My evidence |
|---|---|---|
| Number not on the website | **no** | 2/7 own-site ceiling |
| Finding the right site | yes (search API, annotation) | guessing 2/100 vs manual 7/20 own sites |
| Blocked / JS sites | yes (proxies, headless) | 5/12 correct sites refused or timed out |
| Knowing who should be registered | partly (OCR on PDF accounts) | 82/100 unreadable |
| Holdings / VAT groups | no (not public) | ~half of sample C |

So a bigger crawler reaches the ~30% ceiling reliably, **but not beyond it**. Going beyond needs
a different source.

### What I would do

1. **Make the crawler reach the ceiling:** search API (company number in quotes; name +
   postcode), headless rendering only when needed, crawl subdomains, proxies only for blocked sites.
2. **Find out where aggregators get their numbers:** ask 2–3 vendors for a licence quote and
   compare cost per *verified* number with crawling.
3. **Use the customer's own invoices:** every VAT invoice must show the supplier's number.
   OCR → HMRC → attach. This covers exactly the suppliers they pay.
4. **Deduplicate without VAT:** match each supplier to a Companies House number (name + address).
   Same company number = same supplier.
5. **OCR on PDF accounts** to know who *should* have a number.

### Rough cost (assumptions stated, so you can argue)

| Item | Per company |
|---|---|
| Search API, 2 queries (~$5/1,000) | ~$0.010 |
| Crawl ~6 pages | ~$0.001 |
| Headless for ~30% of sites | ~$0.003 |
| Proxies for ~20% of sites | ~$0.01 |
| HMRC API | $0 (rate-limited) |
| Audit, 1% of records, ~2 min each | ~$0.007 |
| **Total** | **≈ $0.03** |

- Assume ~30% of companies are registered and websites show ~30% of those → yield ≈ 9% →
  **≈ $0.33 per verified number**.
- On ~4.6M non-dormant companies: **≈ $140k for ≈ 400k numbers**. That is the price a data
  licence has to beat.

### What breaks first

1. **HMRC rate limit:** ~3 req/s means ~6 days for 1.5M checks. Monthly re-checks need a higher
   limit agreed with HMRC.
2. **Anti-bot blocking:** already 5/12 sites on a small sample, and it hits /contact pages first.
3. **Group numbers** creeping in as coverage grows (precision drops quietly).
4. **Search quality:** directories and namesakes in results.

### What I would monitor

- Yield and HMRC pass rate per source, weekly.
- Coverage of "expected registered" companies per stratum (the recall proxy).
- Counts of rejected sites (other company number, name only, parked).
- Share of records not re-checked at HMRC within the SLA.
- Monthly audit sample per stratum.
- Mismatches between customer invoices and our records.

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
