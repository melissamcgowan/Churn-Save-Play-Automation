# Churn Save-Play Automation

Watches account health scores from the [Customer Health Score & Churn Prediction Model](../customer-health-score-churn-model) and automatically triggers a save-play the moment an account crosses into critical risk — no manual monitoring required.

## What it does

Reads the health score model's scored output and, for each account, checks two independent triggers:

- **Critical threshold** — health score at or below 40
- **Fast-drop** — score fell 15+ points since the last run, even if it's not critical yet (a fast fall matters as much as a low absolute score, and a static threshold alone would miss it)
- **Warning band** — score at or below 55, flagged but lower urgency

Any account that trips a trigger gets two automated outputs generated together:

1. **A CSM task/alert** — urgency-tagged, with the reasoning attached (which score moved, how fast, and the likely driver pulled from the health model)
2. **A save-sequence email draft** — ready to send, tone-matched to severity (direct outreach for critical/fast-drop, lighter-touch for warning)

Output is written to `save_plays_triggered.csv` and `.json`, sorted by severity.

## Connecting it to your real Health Score model

The script expects a CSV with these columns (adjust `COLUMN_MAP` at the top of the script if your model names them differently):

| column | type | meaning |
|---|---|---|
| `account_id` | str | unique account identifier |
| `account_name` | str | display name |
| `health_score` | int (0–100) | current score |
| `prior_health_score` | int (0–100) | score as of the last run |
| `plan_tier` | str | e.g. Enterprise, Mid-Market, SMB |
| `csm_owner` | str | account's CSM |
| `top_risk_driver` | str | short label for the biggest contributor to the score |

Point `INPUT_CSV` at your model's real output file and it runs against live data instead of the mock dataset. If no file is found, it generates a realistic mock dataset so the pipeline can be demoed end-to-end on its own.

## Tech

Python 3, standard library only (`csv`, `json`, `dataclasses`) — no dependencies to install.

## Run it

```
python3 churn_save_play_automation.py
```

## Part of a larger portfolio

One piece of a broader AI-powered CS automation portfolio — see the [profile README](../) for the full set, including the [Digital Onboarding Sequence Builder](../digital-onboarding-sequence-builder) it complements (onboarding cadence vs. health-triggered save-plays).
