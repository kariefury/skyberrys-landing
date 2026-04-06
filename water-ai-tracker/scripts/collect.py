"""
collect.py  —  Daily data collection for the Water AI Tracker.

Fetches from:
  1. NewsAPI          — news articles
  2. GDELT            — global news (no key required)
  3. Semantic Scholar — academic papers
  4. GitHub API       — new repos tagged water + AI
  5. X/Twitter API    — @skyberrys mention stream (optional)

Writes results to:
  data/articles.json
  data/mentions.json

Run:  python scripts/collect.py
Env vars required (set as GitHub Actions secrets):
  NEWS_API_KEY
  GITHUB_TOKEN        (auto-provided by Actions)
  TWITTER_BEARER_TOKEN  (optional)
"""

import os
import json
import datetime
import requests
import time
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────

COMPANIES = {
    "openai":    ["OpenAI", "ChatGPT", "GPT-4", "GPT-5", "o1", "o3"],
    "anthropic": ["Anthropic", "Claude"],
    "google":    ["Google DeepMind", "Google AI", "Gemini", "Vertex AI"],
    "xai":       ["xAI", "Grok", "Elon Musk AI"],
}
WATER_TERMS = ["water access", "water supply", "clean water", "water scarcity",
               "irrigation", "water quality", "drinking water", "water management"]

DATA_DIR        = Path(__file__).parent.parent / "data"
TODAY           = datetime.date.today().isoformat()
THIRTY_DAYS_AGO = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

NEWS_API_KEY         = os.getenv("NEWS_API_KEY", "")
GITHUB_TOKEN         = os.getenv("GITHUB_TOKEN", "")
TWITTER_BEARER       = os.getenv("TWITTER_BEARER_TOKEN", "")

# ── Startup diagnostic ───────────────────────────────────────────────────────

def print_env_status():
    """Print which secrets arrived — values never printed, only presence."""
    print("── Env var status ──")
    for name, val in [("NEWS_API_KEY", NEWS_API_KEY),
                      ("GITHUB_TOKEN", GITHUB_TOKEN),
                      ("TWITTER_BEARER_TOKEN", TWITTER_BEARER)]:
        print(f"  {name:25s} {'PRESENT' if val else 'MISSING'}")
    print("────────────────────")

# ── Helpers ──────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)

def save_json(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def company_for(text: str):
    text_lower = text.lower()
    for company, aliases in COMPANIES.items():
        if any(alias.lower() in text_lower for alias in aliases):
            return company
    return None

def water_relevant(text: str) -> bool:
    return any(term in text.lower() for term in WATER_TERMS)

def article_entry(date, company, source, title, url, snippet):
    return {
        "date":    date,
        "company": company,
        "source":  source,
        "title":   title,
        "url":     url,
        "snippet": snippet[:400] if snippet else "",
    }

def get_with_retry(url, params=None, headers=None, timeout=20, retries=3, backoff=10):
    """GET with exponential backoff on 429."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                wait = backoff * (2 ** attempt)
                print(f"    429 rate limit — waiting {wait}s before retry {attempt+1}/{retries}")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.HTTPError as e:
            if attempt == retries - 1:
                raise
            time.sleep(backoff)
    return None

# ── 1. NewsAPI ───────────────────────────────────────────────────────────────

def fetch_newsapi(articles: list):
    if not NEWS_API_KEY:
        print("  [newsapi] skipped — NEWS_API_KEY secret not set")
        return
    for company, aliases in COMPANIES.items():
        query = f"({' OR '.join(aliases[:3])}) AND ({' OR '.join(WATER_TERMS[:3])})"
        params = {
            "q": query, "language": "en", "pageSize": 20,
            "from": THIRTY_DAYS_AGO, "sortBy": "relevancy",
            "apiKey": NEWS_API_KEY,
        }
        try:
            resp = get_with_retry("https://newsapi.org/v2/everything", params=params)
            for art in resp.json().get("articles", []):
                text = f"{art.get('title','')} {art.get('description','')}"
                if water_relevant(text):
                    articles.append(article_entry(
                        TODAY, company, "newsapi",
                        art.get("title", ""), art.get("url", ""),
                        art.get("description", "")
                    ))
            time.sleep(1)
        except Exception as e:
            print(f"  [newsapi] {company}: {e}")

# ── 2. GDELT ─────────────────────────────────────────────────────────────────

def fetch_gdelt(articles: list):
    """GDELT DOC 2.0 full-text search — no API key, but strict rate limits."""
    for company, aliases in COMPANIES.items():
        query = f"{aliases[0]} water"
        params = {
            "query": query, "mode": "artlist", "maxrecords": 20,
            "format": "json", "timespan": "7d",   # 7 days not 1 day
        }
        try:
            resp = get_with_retry(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params=params, backoff=15
            )
            for art in resp.json().get("articles", []):
                title = art.get("title", "")
                if water_relevant(title):
                    articles.append(article_entry(
                        TODAY, company, "gdelt",
                        title, art.get("url", ""), art.get("seendate", "")
                    ))
            time.sleep(5)   # GDELT is strict — 5s between company queries
        except Exception as e:
            print(f"  [gdelt] {company}: {e}")

# ── 3. Semantic Scholar ──────────────────────────────────────────────────────

def fetch_semantic_scholar(articles: list):
    for company, aliases in COMPANIES.items():
        query = f"{aliases[0]} water"
        params = {
            "query": query, "limit": 10,
            "fields": "title,abstract,year,externalIds,authors",
        }
        try:
            resp = get_with_retry(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params=params, backoff=15
            )
            for paper in resp.json().get("data", []):
                title    = paper.get("title", "")
                abstract = paper.get("abstract", "") or ""
                if water_relevant(f"{title} {abstract}"):
                    ext = paper.get("externalIds", {})
                    paper_url = (
                        f"https://arxiv.org/abs/{ext['ArXiv']}" if "ArXiv" in ext
                        else f"https://www.semanticscholar.org/paper/{paper.get('paperId','')}"
                    )
                    articles.append(article_entry(
                        TODAY, company, "semantic_scholar",
                        title, paper_url, abstract[:400]
                    ))
            time.sleep(5)   # Semantic Scholar unauthenticated = 100 req/5min
        except Exception as e:
            print(f"  [semantic_scholar] {company}: {e}")

# ── 4. GitHub ─────────────────────────────────────────────────────────────────

def fetch_github(articles: list):
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    queries = [
        "AI water supply",
        "machine learning water quality",
        "deep learning irrigation",
        "AI clean water",
    ]
    seen_repos = set()
    for q in queries:
        params = {
            # Last 30 days — much wider window than just today
            "q": f"{q} pushed:>{THIRTY_DAYS_AGO}",
            "sort": "updated", "order": "desc", "per_page": 10,
        }
        try:
            resp = get_with_retry(
                "https://api.github.com/search/repositories",
                params=params, headers=headers
            )
            for repo in resp.json().get("items", []):
                if repo["full_name"] in seen_repos:
                    continue
                seen_repos.add(repo["full_name"])
                desc    = repo.get("description") or ""
                company = company_for(f"{repo['full_name']} {desc}") or "general"
                articles.append(article_entry(
                    TODAY, company, "github",
                    repo["full_name"],
                    repo["html_url"],
                    f"Stars:{repo['stargazers_count']} | {desc}"
                ))
            time.sleep(2)
        except Exception as e:
            print(f"  [github] query '{q}': {e}")

# ── 5. X / Twitter mentions ───────────────────────────────────────────────────

def fetch_twitter_mentions(mentions_data: dict):
    if not TWITTER_BEARER:
        print("  [twitter] skipped — no TWITTER_BEARER_TOKEN")
        return
    headers = {"Authorization": f"Bearer {TWITTER_BEARER}"}
    water_query = " OR ".join([f'"{t}"' for t in WATER_TERMS[:4]])
    query = f"@skyberrys ({water_query}) -is:retweet lang:en"
    params = {
        "query": query, "max_results": 50,
        "tweet.fields": "created_at,author_id,text",
        "expansions": "author_id", "user.fields": "username",
    }
    try:
        resp = requests.get("https://api.twitter.com/2/tweets/search/recent",
                            headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data  = resp.json()
        users = {u["id"]: u["username"]
                 for u in data.get("includes", {}).get("users", [])}
        for tweet in data.get("data", []):
            text = tweet.get("text", "")
            mentions_data["entries"].append({
                "date": TODAY, "tweet_id": tweet["id"],
                "author": users.get(tweet.get("author_id",""), "unknown"),
                "text": text, "company": company_for(text),
                "created_at": tweet.get("created_at",""),
            })
        print(f"  [twitter] {len(data.get('data', []))} mentions found")
    except Exception as e:
        print(f"  [twitter] error: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"=== Collecting data for {TODAY} ===\n")
    print_env_status()

    articles_data = load_json(DATA_DIR / "articles.json")
    mentions_data = load_json(DATA_DIR / "mentions.json")
    new_articles  = []

    print("[1/4] NewsAPI...")
    fetch_newsapi(new_articles)
    print(f"      {len(new_articles)} articles so far")

    print("[2/4] GDELT...")
    before = len(new_articles)
    fetch_gdelt(new_articles)
    print(f"      +{len(new_articles)-before} articles")

    print("[3/4] Semantic Scholar...")
    before = len(new_articles)
    fetch_semantic_scholar(new_articles)
    print(f"      +{len(new_articles)-before} papers")

    print("[4/4] GitHub...")
    before = len(new_articles)
    fetch_github(new_articles)
    print(f"      +{len(new_articles)-before} repos")

    # De-duplicate by URL before saving
    existing_urls = {e["url"] for e in articles_data["entries"]}
    deduped = [a for a in new_articles if a["url"] not in existing_urls]
    articles_data["entries"].extend(deduped)

    print("\n[+] Twitter @skyberrys mentions...")
    fetch_twitter_mentions(mentions_data)

    save_json(DATA_DIR / "articles.json", articles_data)
    save_json(DATA_DIR / "mentions.json", mentions_data)

    print(f"\nDone. +{len(deduped)} new articles saved.")
    print(f"Total articles : {len(articles_data['entries'])}")
    print(f"Total mentions : {len(mentions_data['entries'])}")

if __name__ == "__main__":
    main()