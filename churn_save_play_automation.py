"""
Churn Save-Play Automation
==========================

Watches account health scores produced by the Customer Health Score &
Churn Prediction Model, and automatically triggers a save-play the moment
an account crosses into risk -- no manual monitoring required.

A "save-play" here means two automated outputs, generated together:
  1. An internal alert/task, with the reasoning attached (which signal
     tripped, how severe, and how much ARR is on the line)
  2. A save-sequence email draft to send (or auto-send) to the account

INPUT
-----
Expects a CSV that is the real output of the Customer Health Score model,
with these columns:

    customer_id        str    unique account identifier
    segment              str    e.g. "Enterprise", "Mid-Market", "SMB"
    arr                   number  annual recurring revenue
    health_score          int     0-100
    health_band            str    the model's own risk label, e.g.
                                  "Healthy" / "At Risk" / "Critical"
    churn_probability      float   0-1, model's predicted churn probability
    churned                bool/0-1  whether the account has already churned

Defaults to reading scored_customers.csv (the output of
melissamcgowan/Customer-Health-Score-Model). If that file isn't found,
the script generates a realistic mock dataset in this exact schema so
the pipeline can be demoed end-to-end on its own.

CONFIG
------
- CRITICAL_BANDS / WARNING_BANDS: which health_band values count as which
  severity. Update these to match the exact label strings your model
  outputs (case-insensitive substring match, so "Critical" and "critical
  risk" both match "critical").
- CRITICAL_PROB / WARNING_PROB: churn_probability cutoffs, used alongside
  (not instead of) the band, since a numeric threshold catches accounts a
  categorical label might not.

Already-churned accounts (churned = true) are excluded from save-plays --
there's nothing left to save. They're a win-back candidate, which is a
separate project on the roadmap.

OUTPUT
------
- save_plays_triggered.csv   -- one row per triggered save-play, sorted by
                                 severity then ARR descending (biggest
                                 revenue at risk surfaces first)
- save_plays_triggered.json  -- same data, structured, with the internal
                                 task and the generated email body
- Console summary
"""

import csv
import json
import random
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Config -- tune without touching logic below
# ---------------------------------------------------------------------------

# health_band strings (lowercased, substring match) that count as each
# severity. Edit these to match your model's actual label text.
CRITICAL_BANDS = ["critical"]
WARNING_BANDS = ["at risk", "at-risk", "warning"]

# churn_probability cutoffs -- these fire independently of health_band,
# so a numeric spike is caught even if the band hasn't been recalculated.
CRITICAL_PROB = 0.65
WARNING_PROB = 0.40

INPUT_CSV = "https://github.com/melissamcgowan/Customer-Health-Score-Model/blob/main/scored_customers.csv"       # real Health Score model output
                                          # (from Customer-Health-Score-Model repo)
OUTPUT_CSV = "save_plays_triggered.csv"
OUTPUT_JSON = "save_plays_triggered.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SavePlay:
    customer_id: str
    segment: str
    arr: float
    health_score: int
    health_band: str
    churn_probability: float
    trigger_reason: str
    severity: str                # "critical" or "warning"
    internal_task: str
    email_subject: str
    email_body: str
    triggered_at: str


# ---------------------------------------------------------------------------
# Mock data (used only if no real health_scores.csv is found)
# ---------------------------------------------------------------------------

def generate_mock_health_data(n=20, seed=7):
    random.seed(seed)
    segments = ["Enterprise", "Mid-Market", "SMB"]
    rows = []
    for i in range(1, n + 1):
        churn_prob = round(random.betavariate(2, 5), 2)  # skews low, some high tail
        health_score = max(5, min(99, round(100 - (churn_prob * 90) - random.randint(-8, 8))))
        if health_score >= 70:
            band = "Healthy"
        elif health_score >= 45:
            band = "At Risk"
        else:
            band = "Critical"
        churned = 1 if (band == "Critical" and random.random() < 0.15) else 0
        rows.append({
            "customer_id": f"CUST-{1000 + i}",
            "segment": random.choice(segments),
            "arr": random.choice([8000, 15000, 24000, 45000, 60000, 90000, 120000, 250000]),
            "health_score": health_score,
            "health_band": band,
            "churn_probability": churn_prob,
            "churned": churned,
        })
    return rows


def load_health_scores(path=INPUT_CSV):
    p = Path(path)
    if not p.exists():
        print(f"No {path} found -- generating mock Health Score model output for demo purposes.\n")
        return generate_mock_health_data()

    with p.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for raw in reader:
            rows.append({
                "customer_id": raw["customer_id"],
                "segment": raw["segment"],
                "arr": float(raw["arr"]),
                "health_score": int(float(raw["health_score"])),
                "health_band": raw["health_band"],
                "churn_probability": float(raw["churn_probability"]),
                "churned": str(raw["churned"]).strip().lower() in ("1", "true", "yes"),
            })
        return rows


# ---------------------------------------------------------------------------
# Trigger logic
# ---------------------------------------------------------------------------

def _band_matches(band, label_list):
    band_l = (band or "").lower()
    return any(label in band_l for label in label_list)


def evaluate_account(row):
    """Return (should_trigger, severity, trigger_reason) for one account."""
    if row["churned"]:
        return False, None, None  # already gone -- not a save-play target

    band = row["health_band"]
    prob = row["churn_probability"]
    score = row["health_score"]

    band_critical = _band_matches(band, CRITICAL_BANDS)
    band_warning = _band_matches(band, WARNING_BANDS)
    prob_critical = prob >= CRITICAL_PROB
    prob_warning = prob >= WARNING_PROB

    if band_critical or prob_critical:
        reasons = []
        if band_critical:
            reasons.append(f"health_band = '{band}'")
        if prob_critical:
            reasons.append(f"churn_probability = {prob:.0%} (>= {CRITICAL_PROB:.0%} threshold)")
        return True, "critical", f"Health score {score}. " + " and ".join(reasons) + "."

    if band_warning or prob_warning:
        reasons = []
        if band_warning:
            reasons.append(f"health_band = '{band}'")
        if prob_warning:
            reasons.append(f"churn_probability = {prob:.0%} (>= {WARNING_PROB:.0%} threshold)")
        return True, "warning", f"Health score {score}. " + " and ".join(reasons) + "."

    return False, None, None


# ---------------------------------------------------------------------------
# Save-play generation (internal task + email draft)
# ---------------------------------------------------------------------------

def build_internal_task(row, severity, trigger_reason):
    urgency = "URGENT" if severity == "critical" else "Review"
    window = "24 hours" if severity == "critical" else "3 business days"
    return (
        f"[{urgency}] Save-play needed: {row['customer_id']} ({row['segment']}, "
        f"${row['arr']:,.0f} ARR)\n"
        f"Reason: {trigger_reason}\n"
        f"Action: Assign to account owner; reach out within {window}. "
        f"Save-play email already drafted below -- personalize with the contact's name "
        f"and specifics before sending."
    )


def build_email(row, severity):
    if severity == "critical":
        subject = "Checking in — want to make sure you're getting value"
        body = (
            f"Hi {{contact_first_name}},\n\n"
            f"I wanted to reach out directly rather than let this go unaddressed -- some signals "
            f"on our end suggest things may not be going as well as they could be.\n\n"
            f"Do you have 15 minutes this week? I'd like to understand what's changed and see if "
            f"there's something we can fix or adjust on our side.\n\n"
            f"— Your Customer Success Team"
        )
    else:
        subject = "A few things that might help"
        body = (
            f"Hi {{contact_first_name}},\n\n"
            f"Wanted to check in -- nothing urgent, but a few signals suggest there might be more "
            f"value to unlock here. Happy to share a quick tip or hop on a call if useful.\n\n"
            f"— Your Customer Success Team"
        )
    return subject, body


def run():
    rows = load_health_scores()
    triggered = []

    for row in rows:
        should_trigger, severity, reason = evaluate_account(row)
        if not should_trigger:
            continue

        subject, body = build_email(row, severity)
        task = build_internal_task(row, severity, reason)

        triggered.append(SavePlay(
            customer_id=row["customer_id"],
            segment=row["segment"],
            arr=row["arr"],
            health_score=row["health_score"],
            health_band=row["health_band"],
            churn_probability=row["churn_probability"],
            trigger_reason=reason,
            severity=severity,
            internal_task=task,
            email_subject=subject,
            email_body=body,
            triggered_at=datetime.now().isoformat(timespec="seconds"),
        ))

    # sort: critical before warning, then by ARR descending (biggest revenue at risk first)
    severity_order = {"critical": 0, "warning": 1}
    triggered.sort(key=lambda sp: (severity_order[sp.severity], -sp.arr))

    write_outputs(triggered)
    print_summary(rows, triggered)


def write_outputs(triggered):
    fieldnames = [
        "customer_id", "segment", "arr", "health_score", "health_band",
        "churn_probability", "trigger_reason", "severity", "internal_task",
        "email_subject", "email_body", "triggered_at",
    ]
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for sp in triggered:
            writer.writerow(asdict(sp))

    with open(OUTPUT_JSON, "w") as f:
        json.dump([asdict(sp) for sp in triggered], f, indent=2)


def print_summary(all_rows, triggered):
    active = [r for r in all_rows if not r["churned"]]
    n_critical = sum(1 for sp in triggered if sp.severity == "critical")
    n_warn = sum(1 for sp in triggered if sp.severity == "warning")
    arr_at_risk = sum(sp.arr for sp in triggered)

    print(f"Evaluated {len(all_rows)} accounts ({len(all_rows) - len(active)} already churned, excluded).")
    print(f"Triggered {len(triggered)} save-plays: {n_critical} critical, {n_warn} warning.")
    print(f"Total ARR represented in triggered save-plays: ${arr_at_risk:,.0f}\n")

    for sp in triggered:
        print(f"[{sp.severity.upper():8s}] {sp.customer_id:12s} {sp.segment:12s} "
              f"${sp.arr:>9,.0f} ARR  score {sp.health_score:3d}  band={sp.health_band:10s} "
              f"churn_prob={sp.churn_probability:.0%}")

    print(f"\nWrote {OUTPUT_CSV} and {OUTPUT_JSON}.")


if __name__ == "__main__":
    run()
