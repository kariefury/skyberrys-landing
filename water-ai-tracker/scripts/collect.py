"""
collect.py  —  Daily data collection for the Water AI Tracker.

Fetches from:
  1. NewsAPI          — news articles
  2. GDELT            — global news (no key required)
  3. Semantic Scholar — academic papers
  4. Reddit (PRAW)    — subreddit posts
  5. GitHub API       — new repos tagged water + AI
  6. X/Twitter API    — @skyberrys mention stream

Writes results to:
  data/articles.json
  data/mentions.json

Run:  python scripts/collect.py
Env vars required (set as GitHub Actions secrets):
  NEWS_API_KEY
  REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
  GITHUB_TOKEN
  TWITTER_BEARER_TOKEN
"""

import os
import json
import datetime
import requests
import time
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────

COMPANIES = {
    "openai":    ["OpenAI", "ChatGPT", "GPT-4", "GPT-5", "o1", "o3"],
    "anthropic": ["Anthropic", "Claude"],
    "google":    ["Google DeepMind", "Google AI", "Gemini", "Vertex AI"],
    "xai":       ["xAI", "Grok", "Elon Musk AI"],
}
WATER_TERMS = ["water access", "water supply", "clean water", "water scarcity",
               "irrigation", "water quality", "drinking water", "water management"]

DATA_DIR   = Path(__file__).parent.parent / "data"
TODAY      = datetime.date.today().isoformat()

NEWS_API_KEY        = os.getenv("NEWS_API_KEY", "")
REDDIT_CLIENT_ID    = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET= os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT   = os.getenv("REDDIT_USER_AGENT", "WaterAITracker/1.0")
GITHUB_TOKEN        = os.getenv("GITHUB_TOKEN", "")
TWITTER_BEARER      = os.getenv("TWITTER_BEARER_TOKEN", "")

# ── Helpers ─────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)

def save_json(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def company_for(text: str) -> str | None:
    """Return the company key if any alias appears in the text, else None."""
    text_lower = text.lower()
    for company, aliases in COMPANIES.items():
        if any(alias.lower() in text_lower for alias in aliases):
            return company
    return None

def water_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(term in text_lower for term in WATER_TERMS)

def article_entry(date, company, source, title, url, snippet):
    return {
        "date":    date,
        "company": company,
        "source":  source,
        "title":   title,
        "url":     url,
        "snippet": snippet[:400] if snippet else "",
    }

# ── 1. NewsAPI ───────────────────────────────────────────────────────────────

def fetch_newsapi(articles: list):
    if not NEWS_API_KEY:
        print("  [newsapi] skipped — no NEWS_API_KEY")
        return
    for company, aliases in COMPANIES.items():
        query = f"({' OR '.join(aliases)}) AND ({' OR '.join(WATER_TERMS[:4])})"
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": query, "language": "en", "pageSize": 20,
            "from": TODAY, "sortBy": "relevancy",
            "apiKey": NEWS_API_KEY,
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            for art in resp.json().get("articles", []):
                text = f"{art.get('title','')} {art.get('description','')}"
                if water_relevant(text):
                    articles.append(article_entry(
                        TODAY, company, "newsapi",
                        art.get("title", ""), art.get("url", ""),
                        art.get("description", "")
                    ))
            time.sleep(0.5)
        except Exception as e:
            print(f"  [newsapi] {company}: {e}")

# ── 2. GDELT ────────────────────────────────────────────────────────────────

def fetch_gdelt(articles: list):
    """GDELT DOC 2.0 full-text search — no API key needed."""
    for company, aliases in COMPANIES.items():
        query = f"{aliases[0]} water"
        url = "https://api.gdeltproject.org/api/v2/doc/doc"
        params = {
            "query": query, "mode": "artlist", "maxrecords": 20,
            "format": "json", "timespan": "1d",
        }
        try:
            resp = requests.get(url, params=params, timeout=20)
            resp.raise_for_status()
            for art in resp.json().get("articles", []):
                title = art.get("title", "")
                if water_relevant(title):
                    articles.append(article_entry(
                        TODAY, company, "gdelt",
                        title, art.get("url", ""), art.get("seendate", "")
                    ))
            time.sleep(0.5)
        except Exception as e:
            print(f"  [gdelt] {company}: {e}")

# ── 3. Semantic Scholar ─────────────────────────────────────────────────────

def fetch_semantic_scholar(articles: list):
    for company, aliases in COMPANIES.items():
        query = f"{aliases[0]} water"
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            "query": query, "limit": 10,
            "fields": "title,abstract,year,externalIds,authors",
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
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
            time.sleep(1)
        except Exception as e:
            print(f"  [semantic_scholar] {company}: {e}")

# ── 4. Reddit ────────────────────────────────────────────────────────────────

def fetch_reddit(articles: list):
    if not REDDIT_CLIENT_ID:
        print("  [reddit] skipped — no REDDIT_CLIENT_ID")
        return
    token_url = "https://www.reddit.com/api/v1/access_token"
    auth = requests.auth.HTTPBasicAuth(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET)
    headers = {"User-Agent": REDDIT_USER_AGENT}
    try:
        tok_resp = requests.post(
            token_url, auth=auth, headers=headers,
            data={"grant_type": "client_credentials"}, timeout=10
        )
        tok_resp.raise_for_status()
        token = tok_resp.json()["access_token"]
    except Exception as e:
        print(f"  [reddit] auth failed: {e}")
        return

    headers["Authorization"] = f"bearer {token}"
    subreddits = ["MachineLearning", "artificial", "environment", "water", "sustainability"]
    for company, aliases in COMPANIES.items():
        query = f"{aliases[0]} water"
        for sub in subreddits:
            url = f"https://oauth.reddit.com/r/{sub}/search.json"
            params = {"q": query, "sort": "new", "limit": 10, "t": "day"}
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=10)
                resp.raise_for_status()
                for post in resp.json()["data"]["children"]:
                    d = post["data"]
                    text = f"{d.get('title','')} {d.get('selftext','')}"
                    if water_relevant(text):
                        articles.append(article_entry(
                            TODAY, company, f"reddit/r/{sub}",
                            d.get("title",""),
                            f"https://reddit.com{d.get('permalink','')}",
                            d.get("selftext","")[:400]
                        ))
                time.sleep(0.5)
            except Exception as e:
                print(f"  [reddit] r/{sub} {company}: {e}")

# ── 5. GitHub ────────────────────────────────────────────────────────────────

def fetch_github(articles: list):
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    queries = [
        "AI water supply",
        "machine learning water quality",
        "deep learning irrigation",
        "AI water access developing",
    ]
    seen_repos = set()
    for q in queries:
        url = "https://api.github.com/search/repositories"
        params = {
            "q": f"{q} pushed:>{TODAY}",
            "sort": "updated", "order": "desc", "per_page": 10,
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            for repo in resp.json().get("items", []):
                if repo["full_name"] in seen_repos:
                    continue
                seen_repos.add(repo["full_name"])
                desc = repo.get("description") or ""
                company = company_for(f"{repo['full_name']} {desc}") or "general"
                articles.append(article_entry(
                    TODAY, company, "github",
                    repo["full_name"],
                    repo["html_url"],
                    f"Stars:{repo['stargazers_count']} | {desc}"
                ))
            time.sleep(1)
        except Exception as e:
            print(f"  [github] query '{q}': {e}")

# ── 6. X / Twitter mentions ──────────────────────────────────────────────────

def fetch_twitter_mentions(mentions_data: dict):
    """
    Fetches recent mentions of @skyberrys that also mention a water term.
    Requires Twitter API v2 Bearer token (Basic or higher tier).
    """
    if not TWITTER_BEARER:
        print("  [twitter] skipped — no TWITTER_BEARER_TOKEN")
        return

    headers = {"Authorization": f"Bearer {TWITTER_BEARER}"}
    # Search for tweets mentioning @skyberrys and at least one water term
    water_query = " OR ".join([f'"{t}"' for t in WATER_TERMS[:4]])
    query = f"@skyberrys ({water_query}) -is:retweet lang:en"
    url = "https://api.twitter.com/2/tweets/search/recent"
    params = {
        "query": query,
        "max_results": 50,
        "tweet.fields": "created_at,author_id,text",
        "expansions": "author_id",
        "user.fields": "username",
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        users = {u["id"]: u["username"]
                 for u in data.get("includes", {}).get("users", [])}
        for tweet in data.get("data", []):
            text    = tweet.get("text", "")
            company = company_for(text)
            entry = {
                "date":       TODAY,
                "tweet_id":   tweet["id"],
                "author":     users.get(tweet.get("author_id",""), "unknown"),
                "text":       text,
                "company":    company,
                "created_at": tweet.get("created_at",""),
            }
            mentions_data["entries"].append(entry)
        print(f"  [twitter] {len(data.get('data',[]))} mentions found")
    except Exception as e:
        print(f"  [twitter] error: {e}")

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"=== Collecting data for {TODAY} ===")

    # Load existing data
    articles_data = load_json(DATA_DIR / "articles.json")
    mentions_data = load_json(DATA_DIR / "mentions.json")

    new_articles = []

    print("\n[1/5] NewsAPI...")
    fetch_newsapi(new_articles)
    print(f"      {len(new_articles)} articles so far")

    print("[2/5] GDELT...")
    before = len(new_articles)
    fetch_gdelt(new_articles)
    print(f"      +{len(new_articles)-before} articles")

    print("[3/5] Semantic Scholar...")
    before = len(new_articles)
    fetch_semantic_scholar(new_articles)
    print(f"      +{len(new_articles)-before} papers")

    print("[4/5] Reddit...")
    before = len(new_articles)
    fetch_reddit(new_articles)
    print(f"      +{len(new_articles)-before} posts")

    print("[5/5] GitHub...")
    before = len(new_articles)
    fetch_github(new_articles)
    print(f"      +{len(new_articles)-before} repos")

    # De-duplicate by URL
    existing_urls = {e["url"] for e in articles_data["entries"]}
    deduped = [a for a in new_articles if a["url"] not in existing_urls]
    articles_data["entries"].extend(deduped)

    print(f"\n[6/6] Twitter @skyberrys mentions...")
    fetch_twitter_mentions(mentions_data)

    # Save
    save_json(DATA_DIR / "articles.json", articles_data)
    save_json(DATA_DIR / "mentions.json", mentions_data)

    print(f"\nDone. +{len(deduped)} new articles saved.")
    print(f"Total articles: {len(articles_data['entries'])}")
    print(f"Total mentions: {len(mentions_data['entries'])}")

if __name__ == "__main__":
    main()
