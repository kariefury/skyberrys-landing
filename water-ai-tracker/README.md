# Water AI Index

> Tracking how well AI companies are contributing to water access — updated daily.

**Live site:** `https://skyberrys.com/water-ai-tracker`

This project lives as a **subfolder** inside the `skyberrys-landing` repo so it
is served cleanly from your custom domain without a separate repository.

---

## How it works

1. **GitHub Actions** runs every day at 06:00 UTC
2. `scripts/collect.py` pulls articles from NewsAPI, GDELT, Semantic Scholar, Reddit and GitHub; and scans X/Twitter for `@skyberrys` mentions
3. `scripts/score.py` computes a 0–100 composite score per company and appends it to `data/scores.json`
4. GitHub Pages serves `index.html` which reads the JSON files and renders the dashboard

---

## Scoring signals

| Signal | Weight | Source |
|---|---|---|
| Compute footprint | 25% | News + research papers on data-centre water/energy use |
| Sentiment | 20% | VADER sentiment of water-related articles |
| Human signal | 30% | Direct messages to @skyberrys on X |
| Evidence-based | 15% | Articles citing quantified water-access outcomes |
| GitHub repos | 10% | New AI × water open-source repos |

Weights are configurable in `data/scores.json → weights`.

---

## Setup

### 1. The folder is already in the repo

You've unzipped it into `skyberrys-landing/water-ai-tracker/`. Install deps locally if you want to run scripts:

```bash
cd water-ai-tracker
pip install -r requirements.txt
```

### 2. Copy the workflow file to the right place

The workflow file must live in the **repo root's** `.github/workflows/` — not inside the subfolder:

```bash
# From the root of skyberrys-landing:
cp water-ai-tracker/.github/workflows/daily.yml .github/workflows/water-ai-daily.yml
```

You can then delete `.github/` from inside `water-ai-tracker/` if you like.

### 3. Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Where to get it |
|---|---|
| `NEWS_API_KEY` | [newsapi.org](https://newsapi.org) — free tier |
| `REDDIT_CLIENT_ID` | [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) |
| `REDDIT_CLIENT_SECRET` | same as above |
| `TWITTER_BEARER_TOKEN` | [developer.twitter.com](https://developer.twitter.com) — Basic tier |

`GITHUB_TOKEN` is provided automatically by GitHub Actions — no setup needed.

### 4. GitHub Pages — already configured

Since `skyberrys-landing` is already serving `skyberrys.com` via GitHub Pages,
`skyberrys.com/water-ai-tracker` will work automatically once you push —
no additional Pages configuration needed.

Go to **Actions → Daily water-AI data collection → Run workflow**.

Or locally:

```bash
python scripts/collect.py
python scripts/score.py
```

---

## Manual mention ingestion

When someone DMs `@skyberrys` on X with a water-access story, add it manually:

```bash
python scripts/ingest_mention.py \
  --author "jane_doe" \
  --text "Used Claude to model water contamination for 3 villages in Kenya. 1200 people now with cleaner water." \
  --company anthropic \
  --tweet_id "1234567890"
```

Or run interactively: `python scripts/ingest_mention.py`

Then re-run the scorer: `python scripts/score.py`

Commit and push — the site updates automatically.

---

## Project structure

```
water-ai-tracker/
├── index.html                  ← public dashboard (GitHub Pages)
├── requirements.txt
├── data/
│   ├── scores.json             ← time-series scores per company
│   ├── articles.json           ← collected articles / papers / repos
│   └── mentions.json           ← @skyberrys X mentions
├── scripts/
│   ├── collect.py              ← fetches from all data sources
│   ├── score.py                ← computes composite scores
│   └── ingest_mention.py       ← manual mention entry
└── .github/
    └── workflows/
        └── daily.yml           ← GitHub Actions cron job
```

---

## Upgrading to Google App Engine

If you later need user logins, a writable API, or real-time updates:

1. Move `data/*.json` → Cloud Firestore
2. Move `scripts/collect.py` + `scripts/score.py` → Cloud Run scheduled jobs
3. Serve `index.html` from App Engine standard (Python 3 / Flask)
4. The frontend JS only needs the fetch URLs updated to point at your Cloud Run endpoints

---

## Contributing

Field reports and pull requests welcome. If you know of an AI-driven water access project
not captured by the automated search, open an issue or DM [@skyberrys](https://twitter.com/skyberrys).
