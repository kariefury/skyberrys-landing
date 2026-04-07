"""
score.py  —  Daily scoring engine for the Water AI Tracker.

Reads:
  data/articles.json
  data/mentions.json

Computes a 0-100 composite score per company (backward-compatible) AND
per model variant (new), then appends both to data/scores.json.

Score hierarchy written per day:
  scores       → { company: float }           — rollup, unchanged
  model_scores → { "company/family/variant": { score, signals } }

Run:  python scripts/score.py
"""

import json
import datetime
import re
from collections import defaultdict
from pathlib import Path

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    VADER_AVAILABLE = True
except ImportError:
    VADER_AVAILABLE = False
    print("  [score] vaderSentiment not installed — sentiment will default to 0.5")

# ── Config ──────────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent.parent / "data"
TODAY    = datetime.date.today().isoformat()

COMPUTE_KEYWORDS = [
    "data center water", "datacenter water", "cooling water", "water consumption",
    "water usage", "water footprint", "energy efficiency", "renewable energy",
    "carbon neutral", "sustainable compute", "green AI", "PUE",
]
EVIDENCE_KEYWORDS = [
    "improved access", "water provided", "liters", "litres", "gallons",
    "households", "villages", "communities", "reduced contamination",
    "water quality improved", "deployed", "implemented", "pilot",
]
COMPUTE_POSITIVE = [
    "efficient", "reduced", "renewable", "sustainable", "recycled",
    "net zero", "carbon neutral", "solar", "wind",
]
COMPUTE_NEGATIVE = [
    "excessive", "increased", "high water", "wasteful", "drought impact",
    "criticized", "scrutiny",
]

QUANTITY_RE = re.compile(
    r"\b(\d[\d,\.]*\s*(liters?|litres?|gallons?|people|households?|villages?|"
    r"communities|families|km|m³|cubic|%))\b",
    re.IGNORECASE
)
MEASURABLE_RE = re.compile(
    r"\b(\d+[\s,]*\d*\s*(liters?|litres?|gallons?|people|households?|"
    r"villages?|communities|families|km|meters?|%))\b",
    re.IGNORECASE
)
NEGATIVE_MENTION_WORDS = [
    "failed", "not working", "doesn't help", "no improvement",
    "worse", "problem", "issue", "broken",
]

# ── Helpers ─────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)

def save_json(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))

def today_entries(entries: list, company: str) -> list:
    return [e for e in entries if e.get("date") == TODAY and e.get("company") == company]

def model_key(company: str, family: str | None, variant: str | None) -> str:
    """Canonical string key for a model, e.g. 'anthropic/claude/claude-sonnet-4-6'."""
    parts = [company]
    if family:
        parts.append(family)
    if variant:
        parts.append(variant)
    return "/".join(parts)

# ── Signal functions ─────────────────────────────────────────────────────────

def score_compute(articles: list) -> float:
    relevant = [
        a for a in articles
        if any(kw in a.get("snippet","").lower() + a.get("title","").lower()
               for kw in COMPUTE_KEYWORDS)
    ]
    if not relevant:
        return 0.5
    scores = []
    for a in relevant:
        text = (a.get("title","") + " " + a.get("snippet","")).lower()
        pos = sum(1 for kw in COMPUTE_POSITIVE if kw in text)
        neg = sum(1 for kw in COMPUTE_NEGATIVE if kw in text)
        if pos + neg == 0:
            scores.append(0.5)
        else:
            scores.append(clamp01(0.3 + (pos / (pos + neg)) * 0.7))
    return sum(scores) / len(scores)


def score_sentiment(articles: list) -> float:
    if not articles:
        return 0.5
    if not VADER_AVAILABLE:
        return 0.5
    analyzer = SentimentIntensityAnalyzer()
    scores   = []
    for a in articles:
        text  = a.get("title","") + ". " + a.get("snippet","")
        score = analyzer.polarity_scores(text)["compound"]
        scores.append((score + 1) / 2)
    return sum(scores) / len(scores)


def score_human(mentions: list) -> float:
    if not mentions:
        return 0.0
    per_mention = []
    for m in mentions:
        text = m.get("text","").lower()
        if any(nw in text for nw in NEGATIVE_MENTION_WORDS):
            per_mention.append(0.3)
        elif MEASURABLE_RE.search(text):
            per_mention.append(0.8)
        else:
            per_mention.append(0.6)
    base = sum(per_mention) / len(per_mention)
    volume_bonus = min(0.2, (len(mentions) - 1) * 0.02)
    return clamp01(base + volume_bonus)


def score_evidence(articles: list) -> float:
    if not articles:
        return 0.0
    weighted_scores = []
    for a in articles:
        text   = (a.get("title","") + " " + a.get("snippet",""))
        source = a.get("source","")
        if any(kw in text.lower() for kw in EVIDENCE_KEYWORDS) or QUANTITY_RE.search(text):
            weight = 1.5 if "semantic_scholar" in source or "arxiv" in source else 1.0
            weighted_scores.append(weight)
    if not weighted_scores:
        return 0.0
    raw = sum(weighted_scores) / (len(articles) * 1.5)
    return clamp01(raw * 3)


def score_github(articles: list) -> float:
    repos = [a for a in articles if a.get("source") == "github"]
    return clamp01(len(repos) / 5)


def compute_signals(articles: list, mentions: list) -> dict:
    return {
        "compute":   score_compute(articles),
        "sentiment": score_sentiment(articles),
        "human":     score_human(mentions),
        "evidence":  score_evidence(articles),
        "github":    score_github(articles),
    }


def compute_composite(signals: dict, weights: dict) -> float:
    total_weight = sum(weights.values())
    if total_weight == 0:
        return 0.0
    score = sum(signals[k] * weights[k] for k in signals) / total_weight
    return round(score * 100, 1)

# ── Grouping helpers ─────────────────────────────────────────────────────────

def group_articles_by_model(articles: list) -> dict[str, list]:
    """
    Returns a dict keyed by model_key strings.
    An article tagged (company, family, variant) is counted in three buckets:
      - company/family/variant  (most specific)
      - company/family          (family rollup)
      - company                 (company rollup)
    This lets us score at each level independently.
    """
    groups: dict[str, list] = defaultdict(list)
    for a in articles:
        co  = a.get("company")
        fam = a.get("model_family")
        var = a.get("model_variant")
        if not co:
            continue
        groups[co].append(a)
        if fam:
            groups[f"{co}/{fam}"].append(a)
        if fam and var:
            groups[f"{co}/{fam}/{var}"].append(a)
    return dict(groups)


def group_mentions_by_model(mentions: list) -> dict[str, list]:
    groups: dict[str, list] = defaultdict(list)
    for m in mentions:
        co  = m.get("company")
        fam = m.get("model_family")
        var = m.get("model_variant")
        if not co:
            continue
        groups[co].append(m)
        if fam:
            groups[f"{co}/{fam}"].append(m)
        if fam and var:
            groups[f"{co}/{fam}/{var}"].append(m)
    return dict(groups)

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"=== Scoring for {TODAY} ===\n")

    scores_data   = load_json(DATA_DIR / "scores.json")
    articles_data = load_json(DATA_DIR / "articles.json")
    mentions_data = load_json(DATA_DIR / "mentions.json")

    weights   = scores_data["weights"]
    companies = scores_data["companies"]

    existing_dates = {e["date"] for e in scores_data["entries"]}
    if TODAY in existing_dates:
        print(f"  Already scored for {TODAY}. Remove entry to re-score.")
        return

    # All articles and mentions for today
    all_today_articles = [e for e in articles_data["entries"] if e.get("date") == TODAY]
    all_today_mentions = [e for e in mentions_data["entries"] if e.get("date") == TODAY]

    art_by_model = group_articles_by_model(all_today_articles)
    men_by_model = group_mentions_by_model(all_today_mentions)

    day_entry = {
        "date":         TODAY,
        "scores":       {},   # company-level rollup (backward compat)
        "signals":      {},   # company-level signals (backward compat)
        "model_scores": {},   # new: keyed by "company/family/variant"
    }

    # ── Company-level scores (backward compatible) ──
    print("Company-level scores:")
    for company in companies:
        arts     = art_by_model.get(company, [])
        mentions = men_by_model.get(company, [])
        signals  = compute_signals(arts, mentions)
        composite = compute_composite(signals, weights)

        day_entry["scores"][company] = composite
        day_entry["signals"][company] = {
            k: round(v, 4) for k, v in signals.items()
        }
        day_entry["signals"][company]["article_count"] = len(arts)
        day_entry["signals"][company]["mention_count"] = len(mentions)

        print(f"  {company:12s}  score={composite:5.1f}  "
              f"compute={signals['compute']:.2f}  sentiment={signals['sentiment']:.2f}  "
              f"human={signals['human']:.2f}  evidence={signals['evidence']:.2f}  "
              f"github={signals['github']:.2f}  "
              f"({len(arts)} articles, {len(mentions)} mentions)")

    # ── Model-level scores (new) ──
    # Score every key that has at least one article or mention
    all_model_keys = set(art_by_model.keys()) | set(men_by_model.keys())
    # Exclude bare company keys — already handled above
    model_keys = [k for k in all_model_keys if "/" in k]

    if model_keys:
        print("\nModel-level scores:")
    for key in sorted(model_keys):
        arts     = art_by_model.get(key, [])
        mentions = men_by_model.get(key, [])
        signals  = compute_signals(arts, mentions)
        composite = compute_composite(signals, weights)

        day_entry["model_scores"][key] = {
            "score": composite,
            "signals": {k: round(v, 4) for k, v in signals.items()},
            "article_count": len(arts),
            "mention_count": len(mentions),
        }
        print(f"  {key:<55s}  score={composite:5.1f}  "
              f"({len(arts)} articles, {len(mentions)} mentions)")

    scores_data["entries"].append(day_entry)
    save_json(DATA_DIR / "scores.json", scores_data)
    print(f"\nSaved to data/scores.json")

if __name__ == "__main__":
    main()