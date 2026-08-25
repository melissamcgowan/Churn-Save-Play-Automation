# Churn Save-Play Automation

Watches account health scores from the [Customer Health Score & Churn Prediction Model](../customer-health-score-churn-model) and automatically triggers a save-play the moment an account crosses into risk; no manual monitoring required.

## What it does

Reads the health score model's scored output and, for each active (non-churned) account, checks two independent signals:

- **`health_band`** - the model's own categorical risk label (matched against configurable `CRITICAL_BANDS` / `WARNING_BANDS`)
- **`churn_probability`** - a numeric cutoff that fires even if the band label hasn't caught up, so a probability spike isn't missed just because it wasn't accompanied by a big score movement

Either signal alone is enough to trigger. An account doesn't need to fail both.

Any account that trips a trigger gets two automated outputs generated together:

1. **An internal task/alert** — urgency-tagged, with the reasoning attached (which signal tripped, at what value) and the account's ARR, so the team can see revenue at risk at a glance
2. **A save-sequence email draft** — ready to personalize and send, tone-matched to severity

Output is written to `save_plays_triggered.csv` and `.json`, sorted by severity, then by ARR descending; the biggest revenue at risk surfaces first.

Already-churned accounts (`churned = true`) are excluded; there's nothing left to save. That's a separate win-back motion, already on the roadmap as its own project.

## Input schema

Matches the real Customer Health Score model output:

| column | type | meaning |
|---|---|---|
| `customer_id` | str | unique account identifier |
| `segment` | str | e.g. Enterprise, Mid-Market, SMB |
| `arr` | number | annual recurring revenue |
| `health_score` | int (0–100) | current score |
| `health_band` | str | the model's own risk label, e.g. Healthy / At Risk / Critical |
| `churn_probability` | float (0–1) | model's predicted churn probability |
| `churned` | bool | whether the account has already churned |

Defaults to reading `scored_customers.csv`, the actual output of [Customer-Health-Score-Model](https://github.com/melissamcgowan/Customer-Health-Score-Model). Drop this script into that repo (or point `INPUT_CSV` at the file's path) to run against live data. If `health_band` uses different label text than "Healthy" / "At Risk" / "Critical", update `CRITICAL_BANDS` and `WARNING_BANDS` at the top of the script — matching is case-insensitive substring, so "Critical" and "critical risk" both match. If no input file is found, it generates a realistic mock dataset in this exact schema so the pipeline demos end-to-end on its own.

## Tech

Python 3, standard library only (`csv`, `json`, `dataclasses`) — no dependencies to install.

## Run it

```
python3 churn_save_play_automation.py
```

## Part of a larger portfolio

One piece of a broader AI-powered CS automation portfolio — see the [profile README](../) for the full set, including the [Digital Onboarding Sequence Builder](../digital-onboarding-sequence-builder) it complements (onboarding cadence vs. health-triggered save-plays).
