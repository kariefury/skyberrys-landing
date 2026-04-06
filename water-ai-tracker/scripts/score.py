"""
score.py  —  Daily scoring engine for the Water AI Tracker.

Reads:
  data/articles.json
  data/mentions.json

Computes a 0-100 composite score per company for today, appends to:
  data/scores.json

Scoring signals (weights configurable in data/scores.json → "weights"):
  1. compute    — articles about AI company data-centre water/energy use
                  higher score = better (more efficient / lower impact)
  2. sentiment  — VADER sentiment of water-related articles
                  positive coverage → higher score
  3. human      — @skyberrys X mentions; weighted by recency & any measurable claim
  4. evidence   — articles/papers citing quantified water-access improvement
  5. github     — count of new AI × water repos pushed today

Run:  python scripts/score.py
"""

import json
import datetime
import re
from pathlib import Path

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    VADER_AVAILABLE = True
except ImportError:
    VADER_AVAILABLE = False
    print("  [score] vaderSentiment not installed — sentiment will default to 0.5")
    print("          Install with: pip install vaderSentiment")

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

# ── Signal 1: Compute footprint ─────────────────────────────────────────────

def score_compute(articles: list) -> float:
    """
    Look for articles about the company's infrastructure water/energy use.
    Positive framing (efficient, renewable) → higher score.
    Negative framing (excessive, criticised) → lower.
    Returns 0–1. Default 0.5 (neutral) when no relevant articles found.
    """
    relevant = [
        a for a in articles
        if any(kw in a.get("snippet","").lower() + a.get("title","").lower()
               for kw in COMPUTE_KEYWORDS)
    ]
    if not relevant:
        return 0.5  # no data → neutral

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

# ── Signal 2: Sentiment ──────────────────────────────────────────────────────

def score_sentiment(articles: list) -> float:
    """
    VADER compound sentiment averaged across all water-related articles.
    VADER compound: -1 (very negative) to +1 (very positive).
    Normalised to 0–1.
    """
    if not articles:
        return 0.5
    if not VADER_AVAILABLE:
        return 0.5

    analyzer = SentimentIntensityAnalyzer()
    scores   = []
    for a in articles:
        text  = a.get("title","") + ". " + a.get("snippet","")
        score = analyzer.polarity_scores(text)["compound"]
        scores.append((score + 1) / 2)  # map -1..1 → 0..1
    return sum(scores) / len(scores)

# ── Signal 3: Human signal (@skyberrys mentions) ────────────────────────────

def score_human(mentions: list) -> float:
    """
    Each mention contributes a base score of 0.6.
    Mentions containing a measurable claim boost to 0.8.
    Mentions with a negative tone drop to 0.3.
    Capped at 1.0 after averaging, with a bonus for volume.
    """
    if not mentions:
        return 0.0

    MEASURABLE = re.compile(
        r"\b(\d+[\s,]*\d*\s*(liters?|litres?|gallons?|people|households?|"
        r"villages?|communities|families|km|meters?|%))\b",
        re.IGNORECASE
    )
    NEGATIVE_WORDS = ["failed", "not working", "doesn't help", "no improvement",
                      "worse", "problem", "issue", "broken"]

    per_mention = []
    for m in mentions:
        text = m.get("text","").lower()
        if any(nw in text for nw in NEGATIVE_WORDS):
            per_mention.append(0.3)
        elif MEASURABLE.search(text):
            per_mention.append(0.8)
        else:
            per_mention.append(0.6)

    base = sum(per_mention) / len(per_mention)
    # Small volume bonus: each mention beyond the first adds 0.02, capped
    volume_bonus = min(0.2, (len(mentions) - 1) * 0.02)
    return clamp01(base + volume_bonus)

# ── Signal 4: Evidence-based ─────────────────────────────────────────────────

def score_evidence(articles: list) -> float:
    """
    Articles/papers citing a quantified, measurable water-access outcome.
    Source weighting: semantic_scholar/arxiv = 1.5×, newsapi/gdelt = 1.0×
    Returns 0–1.
    """
    if not articles:
        return 0.0

    QUANTITY = re.compile(
        r"\b(\d[\d,\.]*\s*(liters?|litres?|gallons?|people|households?|villages?|"
        r"communities|families|km|m³|cubic|%))\b",
        re.IGNORECASE
    )
    weighted_scores = []
    for a in articles:
        text   = (a.get("title","") + " " + a.get("snippet",""))
        source = a.get("source","")
        if any(kw in text.lower() for kw in EVIDENCE_KEYWORDS) or QUANTITY.search(text):
            weight = 1.5 if "semantic_scholar" in source or "arxiv" in source else 1.0
            weighted_scores.append(weight)

    if not weighted_scores:
        return 0.0
    # Normalise: 3 strong evidence articles → ~1.0
    raw = sum(weighted_scores) / (len(articles) * 1.5)
    return clamp01(raw * 3)

# ── Signal 5: GitHub repos ──────────────────────────────────────────────────

def score_github(articles: list) -> float:
    """
    Count GitHub repos pushed today tagged to this company (or general).
    0 repos → 0.0, 5+ repos → 1.0 (linear).
    """
    repos = [a for a in articles if a.get("source") == "github"]
    return clamp01(len(repos) / 5)

# ── Composite scorer ─────────────────────────────────────────────────────────

def compute_composite(signals: dict, weights: dict) -> float:
    """Weighted sum of 0–1 signals → 0–100 final score."""
    total_weight = sum(weights.values())
    if total_weight == 0:
        return 0.0
    score = sum(signals[k] * weights[k] for k in signals) / total_weight
    return round(score * 100, 1)

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"=== Scoring for {TODAY} ===\n")

    scores_data   = load_json(DATA_DIR / "scores.json")
    articles_data = load_json(DATA_DIR / "articles.json")
    mentions_data = load_json(DATA_DIR / "mentions.json")

    weights   = scores_data["weights"]
    companies = scores_data["companies"]

    # Check if today already scored
    existing_dates = {e["date"] for e in scores_data["entries"]}
    if TODAY in existing_dates:
        print(f"  Already scored for {TODAY}. Remove entry to re-score.")
        return

    day_entry = {"date": TODAY, "scores": {}, "signals": {}}

    for company in companies:
        arts     = today_entries(articles_data["entries"], company)
        mentions = today_entries(mentions_data["entries"], company)

        signals = {
            "compute":   score_compute(arts),
            "sentiment": score_sentiment(arts),
            "human":     score_human(mentions),
            "evidence":  score_evidence(arts),
            "github":    score_github(arts),
        }

        composite = compute_composite(signals, weights)
        day_entry["scores"][company]  = composite
        day_entry["signals"][company] = {k: round(v, 4) for k, v in signals.items()}
        day_entry["signals"][company]["article_count"]  = len(arts)
        day_entry["signals"][company]["mention_count"]  = len(mentions)

        print(f"  {company:12s}  score={composite:5.1f}  "
              f"compute={signals['compute']:.2f}  sentiment={signals['sentiment']:.2f}  "
              f"human={signals['human']:.2f}  evidence={signals['evidence']:.2f}  "
              f"github={signals['github']:.2f}  "
              f"({len(arts)} articles, {len(mentions)} mentions)")

    scores_data["entries"].append(day_entry)
    save_json(DATA_DIR / "scores.json", scores_data)
    print(f"\nSaved to data/scores.json")

if __name__ == "__main__":
    main()
