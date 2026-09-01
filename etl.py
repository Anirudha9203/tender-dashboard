"""
etl.py — turns the raw tender workbook into clean, structured records.

The source sheet stores dates as free text ("16th Feb 2026"), the outcome only
inside the Remarks column, and account owners under a dozen different spellings.
Everything here exists to fix one of those problems. Import is idempotent: run it
on the same file twice and you get the same records.
"""

from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

SHEET_NAME = "Tenders"

CATEGORIES = ["Submitted", "Not Qualified", "Under Process", "Cancelled",
              "Empanelment", "Unassigned"]
OUTCOMES = ["Won", "Won - PO Awaited", "Lost", "Disqualified",
            "Under Evaluation", "Cancelled Post-Bid", "N/A"]
NO_BID_REASONS = ["PSU-only restriction", "Consortium not allowed", "MSME preference",
                  "OSM/LMS partner gap", "Solution capability gap", "Financial criteria",
                  "Technical criteria", "Scope/volume mismatch", "Not categorised", ""]
OWNERS = ["Anbarasu", "Ashish", "Prashant", "Vineet", "Deepak", "Vishwajeet",
          "Anand", "Unassigned"]

# FY27 runs 1 Apr 2026 -> 31 Mar 2027
FY_START, FY_END, FY_TARGET = "2026-04-01", "2027-03-31", 150

FIELDS = [
    "id", "srNo", "organization", "city", "owner", "details", "volumeRaw", "volume",
    "contractPeriod", "preBidDate", "queryDate", "submissionDate", "techOpening",
    "techPresentation", "venueVisit", "commercialOpening", "evaluation", "msmePref",
    "tenderFees", "emd", "category", "outcome", "noBidReason", "remarks", "concerns",
    "poReceived", "agreementSigned", "pbg", "contractValue", "rajanRemark",
    "chintanRemark", "pareshRemark", "dataFlags",
]

BLANK: dict[str, Any] = {
    "organization": "", "city": "", "owner": "Unassigned", "details": "", "volumeRaw": "",
    "volume": None, "contractPeriod": "", "preBidDate": "", "queryDate": "",
    "submissionDate": "", "techOpening": "", "techPresentation": "", "venueVisit": "",
    "commercialOpening": "", "evaluation": "", "msmePref": "", "tenderFees": None,
    "emd": None, "category": "Under Process", "outcome": "N/A", "noBidReason": "",
    "remarks": "", "concerns": "", "poReceived": "", "agreementSigned": "",
    "pbg": None, "contractValue": None, "rajanRemark": "", "chintanRemark": "",
    "pareshRemark": "", "dataFlags": [],
}

# Column names in the source workbook, including their trailing spaces.
SRC = {
    "srNo": "Sr. No", "organization": "Organization", "city": "City", "owner": "A/c owner",
    "details": "Tender Details ", "volume": "Candidate volumes ",
    "contractPeriod": "Contract period (Years)", "preBidDate": "Pre Bid meeting date",
    "queryDate": "Last date of Pre bid query submission",
    "submissionDate": "Last Date of Bid Submission", "techOpening": "Tech Opening ",
    "techPresentation": "Tech Presentation", "venueVisit": "Venue visit",
    "commercialOpening": "Commercial Opening ", "evaluation": "QCBS",
    "msmePref": "MSME Pref", "tenderFees": "Tender Fees ", "emd": "EMD",
    "status": "Status", "remarks": "Remarks ", "concerns": "Tender Concerns ",
    "poReceived": "PO received", "agreementSigned": "Agreement signed  (Y/N)",
    "pbg": "PBG %", "rajanRemark": "Rajan remarks",
    "chintanRemark": "Chintan remark", "pareshRemark": "Paresh Remark",
}

# Matches '15:00hrs', '1:00pm', '16.30 hrs' and a trailing bare 'hrs'.
_TIME_RE = re.compile(r"\b\d{1,2}\s*[:.]\s*\d{2}\s*(?:hrs?|am|pm)?|\bhrs?\b", re.IGNORECASE)

# Dates are expected inside FY27 give or take a year; anything else is flagged,
# never silently corrected — '9th Sept 2024' is a typo only the team can resolve.
DATE_MIN, DATE_MAX = "2025-04-01", "2028-03-31"

DATE_FIELDS = {
    "preBidDate": "pre-bid meeting", "queryDate": "pre-bid query cut-off",
    "submissionDate": "bid submission", "techOpening": "technical opening",
    "techPresentation": "technical presentation", "commercialOpening": "commercial opening",
}

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_MONTHS.update({m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)})


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or str(v).strip() in ("", "nan", "NaT")


def clean_text(v: Any) -> str:
    """Collapse whitespace and newlines; the source has plenty of both."""
    if _blank(v):
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def parse_date(v: Any) -> str:
    """Parse the workbook's free-text dates into ISO yyyy-mm-dd.

    Handles '13th April 26', '15th April 2026', '26th May, 15:00hrs'. A missing
    year is inferred from the financial year: Apr-Dec -> 2026, Jan-Mar -> 2027.
    """
    if _blank(v):
        return ""
    if isinstance(v, (pd.Timestamp,)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    # Remove the time of day first. '26th May, 15:00hrs' otherwise reads 15 as the
    # year and silently becomes 2015.
    s = _TIME_RE.sub(" ", s)
    m = re.search(r"(\d{1,2})\s*(?:st|nd|rd|th)?[\s,]+([A-Za-z]+)\.?\s*,?\s*(\d{2,4})?", s)
    if not m:
        return ""
    day, mon_txt, yr = m.group(1), m.group(2).lower().rstrip("."), m.group(3)
    mon = next((v2 for k, v2 in _MONTHS.items() if k.startswith(mon_txt[:3])), None)
    if mon is None:
        return ""
    if yr is None:
        year = 2026 if mon >= 4 else 2027
    else:
        year = int(yr)
        year += 2000 if year < 100 else 0
    try:
        return f"{year:04d}-{mon:02d}-{int(day):02d}"
    except ValueError:
        return ""


def parse_number(v: Any) -> float | None:
    if _blank(v):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    digits = re.sub(r"[^\d.]", "", str(v))
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def parse_volume(v: Any) -> int | None:
    """Pull one comparable number out of '10000-20000', '14 lakhs exams', '40000 + 1500'."""
    if _blank(v):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).lower().replace(",", "")
    if (m := re.search(r"([\d.]+)\s*lakh", s)):
        return int(float(m.group(1)) * 100_000)
    if (m := re.search(r"([\d.]+)\s*crore", s)):
        return int(float(m.group(1)) * 10_000_000)
    if (m := re.search(r"(\d+)\s*-\s*(\d+)", s)):           # a range -> midpoint
        return (int(m.group(1)) + int(m.group(2))) // 2
    nums = [int(x) for x in re.findall(r"\d+", s)]
    return max(nums) if nums else None


_CATEGORY_MAP = {
    "submitted": "Submitted",
    "not submitted": "Not Qualified",
    "did not submit": "Not Qualified",
    "under process": "Under Process",
    "tender getting cancelled": "Cancelled",
    "empanelment": "Empanelment",
}


def map_category(status: Any) -> str:
    """Eight spellings in the sheet collapse to six real states."""
    return _CATEGORY_MAP.get(clean_text(status).lower(), "Unassigned")


def derive_outcome(category: str, remarks: Any) -> str:
    """Read Won/Lost out of Remarks.

    Remarks ONLY. The Tender Concerns column names the rival who won
    ("TCS Won", "Aptech won"), so including it inverts the result on lost bids.
    """
    if category != "Submitted":
        return "N/A"
    r = clean_text(remarks).lower()
    if "lost" in r:
        return "Lost"
    if "won" in r:
        return "Won"
    if "awaiting po" in r:
        return "Won - PO Awaited"
    if "disqualif" in r or "diqualif" in r:        # 'Diqualified' is in the source
        return "Disqualified"
    if "cancel" in r:
        return "Cancelled Post-Bid"
    return "Under Evaluation"


_REASON_RULES = [
    ("PSU-only restriction", ["psu"]),
    ("Consortium not allowed", ["consortium"]),
    ("MSME preference", ["msme"]),
    ("OSM/LMS partner gap", ["osm", "lms"]),
    ("Solution capability gap", ["cannot provide", "capability"]),
    ("Financial criteria", ["profitab", "turnover", "net worth"]),
    ("Technical criteria", ["tech criteria", "criteria limitation", "iso"]),
    ("Scope/volume mismatch", ["small volume", "purchase order", "closed bid"]),
]


def derive_no_bid_reason(category: str, concerns: Any, remarks: Any) -> str:
    """Root-cause tag for tenders we never entered — the biggest leak in the funnel."""
    if category != "Not Qualified":
        return ""
    text = f"{clean_text(concerns)} {clean_text(remarks)}".lower()
    for label, keys in _REASON_RULES:
        if any(k in text for k in keys):
            return label
    return "Not categorised"


_OWNER_CANON = ["Anbarasu", "Ashish", "Prashant", "Vineet", "Deepak", "Vishwajeet", "Anand"]


def fix_owner(v: Any) -> str:
    """Thirteen spellings for eight people: 'prashant', 'Prashant Sharma', 'Prashanr'."""
    s = clean_text(v)
    if not s:
        return "Unassigned"
    low = s.lower()
    for canon in _OWNER_CANON:
        if low.startswith(canon.lower()[:5]):     # absorbs typos and surnames
            return canon
    return s


# A tender fee above this is a mis-keyed field, not a fee (the source has one at 80 lakh).
FEE_SANITY_LIMIT = 200_000


def clean_workbook(source) -> tuple[list[dict], list[str]]:
    """Read the tender workbook and return (records, notes).

    `source` is a path or an uploaded file object. Notes describe what was
    changed so the import is never silent.
    """
    df = pd.read_excel(source, sheet_name=SHEET_NAME)

    # Columns this app adds on export. Absent from the original workbook, so they
    # are read when present and quietly skipped when not.
    OPTIONAL = {"contractValue": "Contract Value", "outcomeCol": "Outcome"}
    opt = {k: v for k, v in OPTIONAL.items() if v in df.columns}

    missing = [c for c in SRC.values() if c not in df.columns]
    if missing:
        raise ValueError(
            "This workbook is missing expected columns: " + ", ".join(missing[:6])
            + ". Import the 'Tenders' sheet in its original layout."
        )

    records: list[dict] = []
    notes: list[str] = []
    skipped = fee_fixes = undated = out_of_range = 0

    for i, row in df.iterrows():
        org = clean_text(row[SRC["organization"]])
        if not org:
            skipped += 1
            continue

        category = map_category(row[SRC["status"]])
        remarks = clean_text(row[SRC["remarks"]])
        concerns = clean_text(row[SRC["concerns"]])

        fee = parse_number(row[SRC["tenderFees"]])
        flags: list[str] = []
        if fee is not None and fee > FEE_SANITY_LIMIT:
            flags.append(f"Tender fee recorded as {fee:,.0f} — implausible, left blank")
            fee = None
            fee_fixes += 1
        if not clean_text(row[SRC["status"]]):
            flags.append("Status missing in source")

        submission = parse_date(row[SRC["submissionDate"]])
        won_here = category == "Submitted" and "won" in remarks.lower() and "lost" not in remarks.lower()
        if won_here and ("contractValue" not in opt or _blank(row.get(opt.get("contractValue")))):
            flags.append("Won, but no contract value recorded — revenue cannot be reported")
        if not submission:
            undated += 1

        rec = dict(BLANK)
        rec.update({
            # Sr. No is the authority; fall back to position. Using the Excel row
            # number here is what previously made the first tender T002.
            "id": f"T{(int(row[SRC['srNo']]) if not _blank(row[SRC['srNo']]) else len(records) + 1):03d}",
            "srNo": int(row[SRC["srNo"]]) if not _blank(row[SRC["srNo"]]) else len(records) + 1,
            "organization": org,
            "city": clean_text(row[SRC["city"]]),
            "owner": fix_owner(row[SRC["owner"]]),
            "details": clean_text(row[SRC["details"]]),
            "volumeRaw": clean_text(row[SRC["volume"]]),
            "volume": parse_volume(row[SRC["volume"]]),
            "contractPeriod": clean_text(row[SRC["contractPeriod"]]),
            "preBidDate": parse_date(row[SRC["preBidDate"]]),
            "queryDate": parse_date(row[SRC["queryDate"]]),
            "submissionDate": submission,
            "techOpening": parse_date(row[SRC["techOpening"]]),
            "techPresentation": parse_date(row[SRC["techPresentation"]]),
            "venueVisit": clean_text(row[SRC["venueVisit"]]),
            "commercialOpening": parse_date(row[SRC["commercialOpening"]]),
            "evaluation": clean_text(row[SRC["evaluation"]]),
            "msmePref": clean_text(row[SRC["msmePref"]]),
            "tenderFees": fee,
            "emd": parse_number(row[SRC["emd"]]),
            "category": category,
            "outcome": derive_outcome(category, remarks),
            "noBidReason": derive_no_bid_reason(category, concerns, remarks),
            "remarks": remarks,
            "concerns": concerns,
            "poReceived": clean_text(row[SRC["poReceived"]]),
            "agreementSigned": clean_text(row[SRC["agreementSigned"]]),
            "pbg": parse_number(row[SRC["pbg"]]),
            "contractValue": (parse_number(row[opt["contractValue"]])
                              if "contractValue" in opt else None),
            "rajanRemark": clean_text(row[SRC["rajanRemark"]]),
            "chintanRemark": clean_text(row[SRC["chintanRemark"]]),
            "pareshRemark": clean_text(row[SRC["pareshRemark"]]),
            "dataFlags": flags,
        })
        # Surface implausible dates rather than guessing what was meant.
        for fld, label in DATE_FIELDS.items():
            val = rec.get(fld)
            if val and not (DATE_MIN <= val <= DATE_MAX):
                rec["dataFlags"].append(
                    f"The {label} date reads {val} — outside FY27, check the source")
                out_of_range += 1

        records.append(rec)

    dated = sum(1 for r in records if r["submissionDate"])
    notes.append(f"Imported {len(records)} tenders.")
    if skipped:
        notes.append(f"Skipped {skipped} row(s) with no organisation name.")
    notes.append(f"Converted {dated} text dates into real dates; {undated} tender(s) had none.")
    notes.append("Derived Won/Lost from the Remarks column into a proper Outcome field.")
    if fee_fixes:
        notes.append(f"Blanked {fee_fixes} implausible tender fee(s) so totals stay honest.")
    if out_of_range:
        notes.append(f"Flagged {out_of_range} date(s) falling outside FY27 for you to check.")
    return records, notes
