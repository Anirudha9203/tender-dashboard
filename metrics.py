"""
metrics.py — every number on the dashboard is computed here, from the records.

Nothing is hardcoded from the review slide. Edit a tender and each figure moves
with it, which is the point: the slide's counts were correct but manual, so they
could only stay correct until the next edit.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from etl import DATE_MAX, DATE_MIN, FY_END, FY_START, FY_TARGET

WON_OUTCOMES = ("Won", "Won - PO Awaited")
OPEN_OUTCOMES = ("Under Evaluation", "Won - PO Awaited")
STALE_DAYS = 90


def _d(iso: str) -> date | None:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def compute(rows: list[dict[str, Any]], today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    fy_start, fy_end = _d(FY_START), _d(FY_END)

    # 'Unassigned' rows have no status in the source, so they sit outside the funnel.
    live = [r for r in rows if r.get("category") != "Unassigned"]
    by_cat = lambda c: [r for r in rows if r.get("category") == c]  # noqa: E731

    submitted = by_cat("Submitted")
    by_out = lambda o: [r for r in submitted if r.get("outcome") == o]  # noqa: E731

    won = [r for r in submitted if r.get("outcome") in WON_OUTCOMES]
    lost, disq = by_out("Lost"), by_out("Disqualified")
    under_eval, post_cancel = by_out("Under Evaluation"), by_out("Cancelled Post-Bid")
    decided = len(won) + len(lost) + len(disq)

    not_qualified = by_cat("Not Qualified")
    under_process, cancelled = by_cat("Under Process"), by_cat("Cancelled")
    empanelment, unassigned = by_cat("Empanelment"), by_cat("Unassigned")

    # --- pace against the FY27 target -------------------------------------
    elapsed_days = max(1, (today - fy_start).days)
    total_days = (fy_end - fy_start).days
    per_month = len(live) / (elapsed_days / 30.44)
    projected = round(per_month * (total_days / 30.44))

    # --- working capital held as EMD on unresolved bids --------------------
    open_bids = [r for r in submitted if r.get("outcome") in OPEN_OUTCOMES]
    emd_blocked = sum(r.get("emd") or 0 for r in open_bids)

    # --- deadlines --------------------------------------------------------
    upcoming = sorted(
        (r for r in rows
         if (d := _d(r.get("submissionDate", ""))) and d >= today
         and r.get("category") in ("Under Process", "Unassigned", "Submitted")),
        key=lambda r: r["submissionDate"],
    )

    # --- monthly activity by bid deadline ---------------------------------
    buckets: dict[str, dict[str, Any]] = {}
    for r in live:
        d = _d(r.get("submissionDate", ""))
        # A date outside the plausible window is a source typo; it stays flagged in
        # Data health but is kept off the chart, where one stray year wrecks the axis.
        if not d or not (_d(DATE_MIN) <= d <= _d(DATE_MAX)):
            continue
        key = d.strftime("%Y-%m")
        b = buckets.setdefault(key, {"month": key, "label": d.strftime("%b %y"),
                                     "Submitted": 0, "Not qualified": 0, "Other": 0})
        cat = r.get("category")
        b["Submitted" if cat == "Submitted"
          else "Not qualified" if cat == "Not Qualified" else "Other"] += 1
    trend = [buckets[k] for k in sorted(buckets)]

    # --- why we could not bid ---------------------------------------------
    reason_counts = Counter(r.get("noBidReason") or "Not categorised" for r in not_qualified)
    reasons = [{"reason": k, "count": v} for k, v in reason_counts.most_common()]

    # --- owner load -------------------------------------------------------
    owners: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"owner": "", "total": 0, "submitted": 0, "won": 0, "lost": 0, "no_bid": 0})
    for r in live:
        o = owners[r.get("owner") or "Unassigned"]
        o["owner"] = r.get("owner") or "Unassigned"
        o["total"] += 1
        if r.get("category") == "Submitted":
            o["submitted"] += 1
        if r.get("outcome") in WON_OUTCOMES:
            o["won"] += 1
        if r.get("outcome") == "Lost":
            o["lost"] += 1
        if r.get("category") == "Not Qualified":
            o["no_bid"] += 1
    owner_rows = sorted(owners.values(), key=lambda x: -x["total"])

    # --- data quality -----------------------------------------------------
    issues: list[dict[str, str]] = []

    def flag(rec, msg, severity="warn"):
        issues.append({"id": rec.get("id", ""), "org": rec.get("organization", "") or rec.get("id", ""),
                       "issue": msg, "severity": severity})

    for r in rows:
        for f in r.get("dataFlags") or []:
            flag(r, f, "high")
        if not r.get("submissionDate"):
            flag(r, "No bid submission date — excluded from deadline and trend views", "high")
        if r.get("category") == "Unassigned":
            flag(r, "No status — sits outside the funnel and is not counted", "high")
        if r.get("emd") is None:
            flag(r, "EMD blank — working-capital exposure understated")
        if (r.get("owner") or "Unassigned") == "Unassigned":
            flag(r, "No account owner assigned")
        if r.get("outcome") == "Under Evaluation":
            d = _d(r.get("submissionDate", ""))
            if d and (today - d).days > STALE_DAYS:
                flag(r, f"Submitted {(today - d).days} days ago and still under evaluation "
                        f"— confirm the result", "high")

    dupes = Counter((r.get("organization") or "").strip().lower() for r in rows if r.get("organization"))
    repeats = sorted(((k.title(), v) for k, v in dupes.items() if v > 1), key=lambda x: -x[1])

    win_rate = (len(won) / decided * 100) if decided else 0.0
    bid_rate = (len(submitted) / len(live) * 100) if live else 0.0
    block_rate = (len(not_qualified) / len(live) * 100) if live else 0.0

    # --- business value ---------------------------------------------------
    # Contract value is not in the source workbook, so these are zero until the
    # team fills it in. Counting how many wins carry a value keeps the headline
    # honest rather than implying six wins are worth whatever has been entered.
    won_valued = [r for r in won if r.get("contractValue")]
    won_value = sum(r.get("contractValue") or 0 for r in won)
    pipeline_valued = [r for r in open_bids if r.get("contractValue")]
    pipeline_value = sum(r.get("contractValue") or 0 for r in open_bids)
    avg_win_value = (won_value / len(won_valued)) if won_valued else 0
    # Every tender with a value entered, regardless of outcome — the honest
    # denominator for "how much have we been tracking", not just what was won.
    all_valued = [r for r in live if r.get("contractValue")]
    total_tracked_value = sum(r.get("contractValue") or 0 for r in all_valued)

    # --- cumulative series for the card sparklines ------------------------
    # Built from the monthly buckets above so a sparkline can never disagree with
    # the trend chart. Cumulative, because a month-by-month count of 1-7 tenders
    # is noise rather than a trend.
    spark: dict[str, list[float]] = {"processed": [], "win_rate": [], "bid_rate": [],
                                     "run_rate": [], "awaiting": [], "required": [],
                                     "contract_value": []}
    run_tot = run_sub = run_won = run_dec = run_eval = 0
    run_value = 0.0
    for i, b in enumerate(trend, start=1):
        month_rows = [r for r in live
                      if (d := _d(r.get("submissionDate", ""))) and d.strftime("%Y-%m") == b["month"]]
        run_tot += len(month_rows)
        run_sub += sum(1 for r in month_rows if r.get("category") == "Submitted")
        run_won += sum(1 for r in month_rows if r.get("outcome") in WON_OUTCOMES)
        run_dec += sum(1 for r in month_rows
                       if r.get("outcome") in WON_OUTCOMES + ("Lost", "Disqualified"))
        run_eval += sum(1 for r in month_rows if r.get("outcome") == "Under Evaluation")
        run_value += sum(r.get("contractValue") or 0 for r in month_rows
                         if r.get("outcome") in WON_OUTCOMES)
        spark["processed"].append(run_tot)
        spark["win_rate"].append(run_won / run_dec * 100 if run_dec else 0)
        spark["bid_rate"].append(run_sub / run_tot * 100 if run_tot else 0)
        spark["run_rate"].append(run_tot / i)
        # What the remaining target would have demanded at each point in the year.
        b_end = _d(b["month"] + "-01")
        left_months = max(0.5, (fy_end - b_end).days / 30.44) if b_end else 0.5
        spark["required"].append(max(0.0, (FY_TARGET - run_tot) / left_months))
        spark["awaiting"].append(run_eval)
        spark["contract_value"].append(run_value)

    # --- pace arithmetic --------------------------------------------------
    months_left = max(0.1, (fy_end - today).days / 30.44)
    required_run_rate = max(0.0, (FY_TARGET - len(live)) / months_left)
    attainment = (projected / FY_TARGET * 100) if FY_TARGET else 0

    # --- largest opportunities by candidate volume ------------------------
    # Contract value is unrecorded, so candidate volume is the only size signal
    # the workbook actually carries.
    volumed = sorted((r for r in live if r.get("volume")),
                     key=lambda r: -(r.get("volume") or 0))
    volume_open = sum(r.get("volume") or 0 for r in live
                      if r.get("category") in ("Submitted", "Under Process")
                      and r.get("outcome") not in ("Lost", "Disqualified"))

    return {
        "rows": rows, "live": live, "submitted": submitted, "won": won, "lost": lost,
        "disq": disq, "under_eval": under_eval, "post_cancel": post_cancel,
        "not_qualified": not_qualified, "under_process": under_process,
        "cancelled": cancelled, "empanelment": empanelment, "unassigned": unassigned,
        "open_bids": open_bids,
        "decided": decided, "win_rate": win_rate, "bid_rate": bid_rate, "block_rate": block_rate,
        "target": FY_TARGET, "processed": len(live), "pending": FY_TARGET - len(live),
        "per_month": per_month, "projected": projected, "on_pace": projected >= FY_TARGET,
        "emd_blocked": emd_blocked, "upcoming": upcoming, "trend": trend,
        "spark": spark, "months_left": months_left,
        "required_run_rate": required_run_rate, "attainment": attainment,
        "volumed": volumed, "volume_open": volume_open,
        "won_value": won_value, "won_valued": won_valued, "avg_win_value": avg_win_value,
        "all_valued": all_valued, "total_tracked_value": total_tracked_value,
        "pipeline_value": pipeline_value, "pipeline_valued": pipeline_valued,
        # Where the year itself has got to — the honest benchmark for the target gauge.
        "year_elapsed_pct": min(100.0, elapsed_days / total_days * 100),
        "reasons": reasons, "owner_rows": owner_rows,
        "issues": issues, "repeats": repeats,
        "pct_of_target": (len(live) / FY_TARGET * 100) if FY_TARGET else 0,
    }


def inr(value: float | int | None) -> str:
    """Indian digit grouping: 1,04,39,532."""
    if value is None:
        return "—"
    n = int(round(float(value)))
    sign, n = ("-", -n) if n < 0 else ("", n)
    s = str(n)
    if len(s) <= 3:
        return f"{sign}₹{s}"
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return f"{sign}₹{','.join(parts)},{tail}"


def inr_short(value: float | int | None) -> str:
    """Compact Indian units: crore above 1,00,00,000, lakh above 1,00,000."""
    if value is None:
        return "—"
    v = float(value)
    if v >= 1e7:
        s = f"{v / 1e7:.2f}".rstrip("0").rstrip(".")
        return f"₹{s} Cr"
    if v >= 1e5:
        s = f"{v / 1e5:.2f}".rstrip("0").rstrip(".")
        return f"₹{s} L"
    return inr(v)


def cr(value: float | int | None) -> str:
    """Contract value is entered and stored in ₹ crore (matching the source
    workbook's own 'Contract Value' column), not rupees — so inr_short(), which
    assumes a rupee figure and auto-scales, badly misreads a small crore number
    (e.g. 0.9 Cr became '₹1'). This formats a crore-denominated figure directly,
    at a fixed two-decimal precision so ₹0.15 Cr and ₹0.05 Cr stay distinguishable
    rather than both rounding to the same displayed number."""
    if value in (None, 0):
        return "—"
    return f"₹{float(value):.2f} Cr"


def fmt_date(iso: str) -> str:
    d = _d(iso or "")
    return d.strftime("%d %b %y") if d else "—"