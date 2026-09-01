# Tender Repository & Performance Dashboard

A Streamlit app that replaces the manual tender tracker with a live repository:
enter or edit a tender once, and every figure on the dashboard recomputes from
the records.

---

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py
```

It opens at `http://localhost:8501`.

The repository already contains your 59 tenders from
`2026-27__tender_status1.xlsx`, so it works immediately. To start from an empty
repository instead, delete the `data/` folder before the first run — the app
will then ask you to upload the workbook.

---

## Where the data lives

No database. Everything sits in a `data/` folder beside `app.py`:

```
tender_app/
├── app.py              Streamlit interface
├── etl.py              Workbook cleaning and import rules
├── store.py            Reads and writes the data folder
├── metrics.py          Every dashboard figure is computed here
├── exporter.py         Writes Excel back in the source workbook layout
├── assets/logo.png     Shown in the header
└── data/
    ├── tenders.json    The repository — one record per tender
    ├── meta.json       Last saved timestamp and action
    └── backups/        Timestamped copy of the 20 most recent saves
```

Every save rewrites `tenders.json` atomically and copies the previous version
into `backups/`, so a bad edit is always recoverable from the
**Import & backups** tab. Reopening the app reads whatever is in `data/` — you
never re-upload unless you want to.

Back up the repository by copying the `data/` folder. Move it to another
machine by copying the same folder.

---

## The four tabs

**Dashboard** — the funnel from the review slide, made live, plus the metrics
the slide did not carry: win rate, bid conversion, run rate against target, and
EMD tied up in unresolved bids. A conversion funnel and two completion gauges
sit above it; the marker on the target gauge is the share of the year already
elapsed, so being ahead of the marker means being ahead of pace. **Every figure
in the funnel is clickable** — select "Won · 6" or "PSU-only restriction · 4"
and the tenders behind it are listed underneath.

**Repository** — search and filter all tenders, download the filtered view as
CSV, and open any record for editing.

**Add / edit tender** — every field from the workbook. Account owner offers the
existing names, and "Add a new name" lets you type someone new, who then becomes
a normal option everywhere. Outcome is only writable
when the stage is *Submitted*, and the no-bid reason only when the stage is
*Not Qualified*, so the two can never contradict each other.

**Data health** — reconciliation against the review slide, open data issues, and
what to fix in the source workbook.

**Import & backups** — bring in another workbook (merge or replace), export the
repository to CSV or Excel, roll back to any earlier save, and reset.

The Excel export uses the source workbook's own sheet name and its 27 columns in
the same order, with real dates and the original `Status` wording, so it drops
straight back into the existing process. Two columns are appended at the end —
**Outcome** and **Reason not qualified** — because neither exists in the original
layout and neither can be recovered from it. Exporting and re-importing gives
byte-for-byte identical counts.

**Reset** clears every tender and returns the app to the upload screen. It asks
you to type RESET first, and still writes a backup to `data/backups/` before
clearing, so an accidental reset is recoverable from disk.

*Data health is currently hidden.* Its checks still run — the flags are stored on
each record — but the tab is commented out in `app.py`. To bring it back, restore
the `tab_health` entry in the `st.tabs(...)` call and remove the `# ` prefixes
from the `DATA HEALTH TAB (hidden)` block.

---

## What the analysis found

### The slide's numbers are right, but they could not stay right

The three buckets tie exactly to the `Status` column: **24 qualified &
submitted, 20 not qualified, 14 pipeline — 58 processed**. That is the good
news. The problem is that every one of those figures was counted by hand, and
Won/Lost was never a field at all — it lived only inside free-text remarks like
"Won the bid". No formula could ever count it, so the numbers were correct only
until the next edit. They are now derived fields.

### One figure on the slide is wrong

The slide says **"48 tenders processed since 1st April 26"** in one box and
**58** in another. The workbook supports 58.

### Eligibility is the bottleneck, not competitiveness

This is the most important finding.

| Stage | Count | Rate |
|---|---|---|
| Processed | 58 | — |
| Reached submission | 24 | 41% |
| **Blocked before bidding** | **20** | **34%** |
| Won (of 14 decided bids) | 6 | **43%** |

A 43% win rate on bids actually entered is strong. The loss is happening
earlier: a third of all tenders are excluded before a bid can be entered.
Converting even a third of those adds roughly 7 bids and 3 more wins — more
than any pricing change would deliver.

The root causes, now a tracked field:

| Reason | Count |
|---|---|
| PSU-only restriction | 4 |
| MSME preference | 3 |
| Consortium not allowed | 2 |
| OSM/LMS partner gap | 2 |
| Solution capability gap | 2 |
| Technical criteria | 2 |
| Scope/volume mismatch | 2 |
| Financial criteria (3-yr profitability) | 1 |
| Not categorised | 2 |

PSU-only and MSME preference block 7 tenders between them. Neither is fixable
by bidding harder — both need a channel answer, the same shape of fix as the
MKCL tie-up already used for the OSM/LMS gap.

### The target is on pace — the "92 pending" framing misleads

At 58 processed in the first 4.6 months of FY27, the run rate is **12.5 per
month**, which projects to **150 by 31 March 2027** against a target of 150.
The slide presents 92 pending as a gap; it is simply the rest of the year.
Report pace, not the remainder.

### Working capital is sitting idle

**₹24.6 lakh** of EMD is tied up in 9 unresolved bids. EMD is refundable but
idle, and several bids have been in evaluation for over 90 days. The Data
health tab lists the stale ones oldest first.

---

## What was cleaned on import

| Problem in the workbook | Fix |
|---|---|
| Dates stored as text — "16th Feb 2026" | 56 converted to real dates; 3 had none |
| `Status` had 8 spellings for 6 states | Fixed dropdown |
| `A/c owner` had 13 spellings for 8 people | Merged; now a dropdown |
| Won/Lost buried in free-text remarks | Promoted to an `Outcome` field |
| A tender fee recorded as ₹80,00,000 | Blanked rather than allowed to distort totals |
| One row with no organisation | Skipped |
| One row with no status | Kept, flagged, held outside the funnel |
| `26th May, 15:00hrs` parsed as the year 2015 | Time of day stripped before parsing |
| Two submission dates typed as 2024 | Flagged for review, never silently changed |
| Tender IDs started at T002 | The header row was being counted; IDs now follow `Sr. No` from T001 |

## Contract value — the field that unlocks revenue reporting

The source workbook has no contract value column, so counts were all that could
ever be reported: a ₹5 crore loss and a ₹5 lakh loss looked identical.

*The Business won card is currently commented out on the dashboard* — an empty
card is noise. Re-enable it by un-commenting the `kpi(c[5], "Business won", ...)`
block in `app.py` and changing `st.columns(5)` to `st.columns(6)` just above it.

**Contract value is now a field on the form.** Enter the order value on a won
tender (or the estimate while bidding) and the **Business won** card fills in,
formatted in lakh and rolling over to crore past ₹1,00,00,000. The card also
shows how many of the wins actually carry a value — six wins with two values
entered reads "Across 2 of 6 wins", so the headline is never mistaken for the
full picture.

Contract value is written to the Excel export as its own column and read back on
import, so it survives a full round-trip.

`PO received` and `Agreement signed` are still empty on every row, including the
wins, so the funnel stops at "won" and cannot yet show conversion into invoiced
revenue.

## Logo

The header looks for the logo in this order, using the first that exists:

```
assets/logo.png
assets/DEX_Logo.png
DEX_Logo.png                (next to app.py)
logo.png                    (next to app.py)
C:\Users\07826\Desktop\Python Projects\Tender Dashboard\DEX_Logo.png
```

If none is found the app says so and lists these paths on screen. The bundled
copy is at `assets/logo.png` — if the header is blank, that folder did not come
across with the rest of the files.
