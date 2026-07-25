"""
Churn Save-Play Automation
==========================

Watches account health scores produced by the Customer Health Score &
Churn Prediction Model, and automatically triggers a save-play the moment
an account crosses into critical risk -- no manual monitoring required.

A "save-play" here means two automated outputs, generated together:
  1. An internal alert/task for the CSM, with the reasoning attached
     (which score dropped, how fast, and why it tripped the threshold)
  2. A save-sequence email draft to send (or auto-send) to the account

INPUT
-----
Expects a CSV that is the output of the Customer Health Score model,
with (at minimum) these columns:

    account_id        str   unique account identifier
    account_name       str   display name
    health_score       int   0-100, current score
    prior_health_score  int   0-100, score as of the last run
    plan_tier          str   e.g. "Enterprise", "Mid-Market", "SMB"
    csm_owner          str   name/email of the account's CSM
    top_risk_driver     str   short label for the biggest contributor
                              to the score (e.g. "Login frequency down",
                              "Support ticket spike", "Key contact left")

If no CSV is supplied, the script generates a realistic mock dataset so
the pipeline can be demoed end-to-end on its own.

If your actual Health Score model output uses different column names,
update COLUMN_MAP below rather than the rest of the script.

OUTPUT
------
- save_plays_triggered.csv   -- one row per triggered save-play
- save_plays_triggered.json  -- same data, structured, with the CSM task
                                 and the generated email body
- Console summary
"""

import csv
import json
import random
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Config -- tune thresholds here without touching logic below
# ---------------------------------------------------------------------------

# Map this script's expected column names -> your actual model's column
# names, if they differ. Left side never changes; edit the right side.
COLUMN_MAP = {
    "account_id": "account_id",
    "account_name": "account_name",
    "health_score": "health_score",
    "prior_health_score": "prior_health_score",
    "plan_tier": "plan_tier",
    "csm_owner": "csm_owner",
    "top_risk_driver": "top_risk_driver",
}

CRITICAL_THRESHOLD = 40      # health_score at/below this = critical risk
WARNING_THRESHOLD = 55       # health_score at/below this = elevated risk
FAST_DROP_POINTS = 15        # a drop of this many points since last run
                              # triggers a save-play even above the
                              # critical threshold (a fast fall matters
                              # as much as a low absolute score)

INPUT_CSV = "health_scores.csv"          # expected model output
OUTPUT_CSV = "save_plays_triggered.csv"
OUTPUT_JSON = "save_plays_triggered.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SavePlay:
    account_id: str
    account_name: str
    plan_tier: str
    csm_owner: str
    health_score: int
    prior_health_score: int
    score_delta: int
    top_risk_driver: str
    trigger_reason: str
    severity: str                # "critical" or "fast_drop" or "warning"
    csm_task: str
    email_subject: str
    email_body: str
    triggered_at: str


# ---------------------------------------------------------------------------
# Mock data (used only if no real health_scores.csv is found)
# ---------------------------------------------------------------------------

def generate_mock_health_data(n=18, seed=7):
    random.seed(seed)
    tiers = ["Enterprise", "Mid-Market", "SMB"]
    csms = ["Dana Reyes", "Marcus Webb", "Priya Nair", "Tom Ashford"]
    risk_drivers = [
        "Login frequency down 40% MoM",
        "Support ticket spike (3x baseline)",
        "Key contact left the account",
        "Feature adoption stalled since renewal",
        "NPS response: detractor",
        "Seats unused > 60 days",
        "Executive sponsor unresponsive",
        "Contract renewal in 30 days, usage flat",
    ]
    rows = []
    for i in range(1, n + 1):
        prior = random.randint(35, 90)
        # bias some accounts toward a real drop so the demo has signal
        drop = random.choice([0, 0, 3, 5, 8, 12, 18, 22])
        current = max(5, prior - drop)
        rows.append({
            "account_id": f"ACC-{1000 + i}",
            "account_name": f"Account {i}",
            "health_score": current,
            "prior_health_score": prior,
            "plan_tier": random.choice(tiers),
            "csm_owner": random.choice(csms),
            "top_risk_driver": random.choice(risk_drivers) if drop > 0 else "No significant drivers",
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
            row = {key: raw[COLUMN_MAP[key]] for key in COLUMN_MAP}
            row["health_score"] = int(row["health_score"])
            row["prior_health_score"] = int(row["prior_health_score"])
            rows.append(row)
        return rows


# ---------------------------------------------------------------------------
# Trigger logic
# ---------------------------------------------------------------------------

def evaluate_account(row):
    """Return (should_trigger, severity, trigger_reason) for one account."""
    score = row["health_score"]
    prior = row["prior_health_score"]
    delta = prior - score  # positive = score dropped

    if score <= CRITICAL_THRESHOLD:
        return True, "critical", f"Health score at {score} -- at or below the critical threshold ({CRITICAL_THRESHOLD})."

    if delta >= FAST_DROP_POINTS:
        return True, "fast_drop", f"Health score fell {delta} points since the last run ({prior} -> {score}) -- fast-drop trigger, independent of absolute score."

    if score <= WARNING_THRESHOLD:
        return True, "warning", f"Health score at {score} -- inside the elevated-risk band ({WARNING_THRESHOLD} or below)."

    return False, None, None


# ---------------------------------------------------------------------------
# Save-play generation (CSM task + email draft)
# ---------------------------------------------------------------------------

def build_csm_task(row, severity, trigger_reason):
    urgency = {"critical": "URGENT", "fast_drop": "URGENT", "warning": "Review"}[severity]
    return (
        f"[{urgency}] Save-play needed: {row['account_name']} ({row['plan_tier']})\n"
        f"Reason: {trigger_reason}\n"
        f"Likely driver: {row['top_risk_driver']}\n"
        f"Owner: {row['csm_owner']}\n"
        f"Action: Reach out within {'24 hours' if urgency == 'URGENT' else '3 business days'}; "
        f"reference the save-play email already drafted below."
    )


def build_email(row, severity):
    name = row["account_name"]
    driver = row["top_risk_driver"]

    if severity in ("critical", "fast_drop"):
        subject = f"Checking in, {name} — want to make sure you're getting value"
        body = (
            f"Hi {{contact_first_name}},\n\n"
            f"I noticed some signals on our end ({driver.lower()}) and wanted to reach out directly "
            f"rather than let it go unaddressed.\n\n"
            f"Do you have 15 minutes this week? I'd like to understand what's changed and see if "
            f"there's something we can fix or adjust on our side.\n\n"
            f"— {row['csm_owner']}"
        )
    else:
        subject = f"A few things that might help, {name}"
        body = (
            f"Hi {{contact_first_name}},\n\n"
            f"Wanted to flag {driver.lower()} — nothing urgent, but worth a look. "
            f"Happy to share a quick tip or hop on a call if useful.\n\n"
            f"— {row['csm_owner']}"
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
        task = build_csm_task(row, severity, reason)

        triggered.append(SavePlay(
            account_id=row["account_id"],
            account_name=row["account_name"],
            plan_tier=row["plan_tier"],
            csm_owner=row["csm_owner"],
            health_score=row["health_score"],
            prior_health_score=row["prior_health_score"],
            score_delta=row["prior_health_score"] - row["health_score"],
            top_risk_driver=row["top_risk_driver"],
            trigger_reason=reason,
            severity=severity,
            csm_task=task,
            email_subject=subject,
            email_body=body,
            triggered_at=datetime.now().isoformat(timespec="seconds"),
        ))

    # sort: critical / fast_drop first, then by lowest score
    severity_order = {"critical": 0, "fast_drop": 1, "warning": 2}
    triggered.sort(key=lambda sp: (severity_order[sp.severity], sp.health_score))

    write_outputs(triggered)
    print_summary(rows, triggered)


def write_outputs(triggered):
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(triggered[0]).keys()) if triggered else [
            "account_id", "account_name", "plan_tier", "csm_owner", "health_score",
            "prior_health_score", "score_delta", "top_risk_driver", "trigger_reason",
            "severity", "csm_task", "email_subject", "email_body", "triggered_at",
        ])
        writer.writeheader()
        for sp in triggered:
            writer.writerow(asdict(sp))

    with open(OUTPUT_JSON, "w") as f:
        json.dump([asdict(sp) for sp in triggered], f, indent=2)


def print_summary(all_rows, triggered):
    n_critical = sum(1 for sp in triggered if sp.severity == "critical")
    n_fast = sum(1 for sp in triggered if sp.severity == "fast_drop")
    n_warn = sum(1 for sp in triggered if sp.severity == "warning")

    print(f"Evaluated {len(all_rows)} accounts.")
    print(f"Triggered {len(triggered)} save-plays: {n_critical} critical, {n_fast} fast-drop, {n_warn} warning.\n")

    for sp in triggered:
        print(f"[{sp.severity.upper():9s}] {sp.account_name:15s} score {sp.prior_health_score} -> {sp.health_score}  "
              f"({sp.top_risk_driver}) -- owner: {sp.csm_owner}")

    print(f"\nWrote {OUTPUT_CSV} and {OUTPUT_JSON}.")


if __name__ == "__main__":
    run()
