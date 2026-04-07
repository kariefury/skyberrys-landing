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

Run:  python -u scripts/collect.py
Env vars required (set as GitHub Actions secrets):
  NEWS_API_KEY
  GITHUB_TOKEN          (auto-provided by Actions)
  TWITTER_BEARER_TOKEN  (optional)
"""

import os
import json
import datetime
import requests
import time
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────────────────

# Three-level model registry: company → family → { variant: [aliases], "_family_aliases": [...] }
# Aliases are matched case-insensitively against full article text.
# Variants are tried first (most specific); family aliases are the fallback.
MODELS = {
    "anthropic": {
        "claude": {
            "claude-opus-4-6":   ["claude opus 4", "claude-opus-4", "claude opus-4",
                                  "claude opus 4.6", "claude-opus-4-6"],
            "claude-sonnet-4-6": ["claude sonnet 4", "claude-sonnet-4", "claude sonnet-4",
                                  "claude sonnet 4.6", "claude-sonnet-4-6",
                                  "sonnet 4.6", "sonnet-4-6"],
            "claude-haiku-4-5":  ["claude haiku 4", "claude-haiku-4", "claude haiku-4",
                                  "claude haiku 4.5", "claude-haiku-4-5"],
            "claude-generic":    ["claude sonnet", "claude opus", "claude haiku",
                                  "claude instant", "claude-2", "claude-3"],
            "_family_aliases":   ["anthropic", "claude"],
        },
    },
    "openai": {
        "gpt": {
            "gpt-4o":        ["gpt-4o", "gpt4o", "gpt 4o"],
            "gpt-4o-mini":   ["gpt-4o-mini", "gpt4o-mini", "gpt 4o mini"],
            "gpt-4.5":       ["gpt-4.5", "gpt 4.5"],
            "o3":            [" o3 ", "openai o3", "openai's o3", "model o3"],
            "o1":            [" o1 ", "openai o1", "openai's o1", "model o1"],
            "gpt-4-generic": ["gpt-4", "gpt4", "gpt 4"],
            "gpt-3.5":       ["gpt-3.5", "gpt 3.5", "chatgpt 3"],
            "_family_aliases": ["openai", "chatgpt", "gpt"],
        },
        "sora": {
            "sora-2":  ["sora 2", "sora-2"],
            "sora":    ["sora"],
            "_family_aliases": ["sora"],
        },
        "whisper": {
            "whisper": ["whisper"],
            "_family_aliases": ["whisper"],
        },
    },
    "google": {
        "gemini": {
            "gemini-2.5-pro":   ["gemini 2.5 pro", "gemini-2.5-pro", "gemini pro 2.5"],
            "gemini-2.5-flash": ["gemini 2.5 flash", "gemini-2.5-flash", "gemini flash 2.5"],
            "gemini-2.0-pro":   ["gemini 2.0 pro", "gemini-2.0-pro"],
            "gemini-2.0-flash": ["gemini 2.0 flash", "gemini-2.0-flash", "gemini flash"],
            "gemini-1.5-pro":   ["gemini 1.5 pro", "gemini-1.5-pro"],
            "gemini-1.5-flash": ["gemini 1.5 flash", "gemini-1.5-flash"],
            "gemini-generic":   ["gemini"],
            "_family_aliases":  ["google deepmind", "google ai", "google", "deepmind", "gemini"],
        },
        "veo": {
            "veo-2": ["veo 2", "veo-2"],
            "veo":   ["veo"],
            "_family_aliases": ["veo"],
        },
    },
    "xai": {
        "grok": {
            "grok-3":       ["grok 3", "grok-3", "grok3"],
            "grok-2":       ["grok 2", "grok-2", "grok2"],
            "grok-generic": ["grok"],
            "_family_aliases": ["xai", "x.ai", "grok", "elon musk ai"],
        },
    },
}

WATER_TERMS = [
    "water access", "water supply", "clean water", "water scarcity",
    "irrigation", "water quality", "drinking water", "water management",
    "water treatment", "water sanitation", "water purification", "potable water",
]

# Compound words where 'water' has nothing to do with water access.
# Scrubbed out before any water_relevant() check — applies to all sources.
WATER_FALSE_POSITIVES = [
    "watermark", "waterfall", "waterloo", "underwater",
    "watercolor", "watercolour", "watertight", "waterproof",
    "waterfront", "watergate", "water-cooled", "watercooled",
    "backwater", "deepwater", "watershed",
]

DATA_DIR        = Path(__file__).parent.parent / "data"
TODAY           = datetime.date.today().isoformat()
THIRTY_DAYS_AGO = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

NEWS_API_KEY   = os.getenv("NEWS_API_KEY", "")
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "")
TWITTER_BEARER = os.getenv("TWITTER_BEARER_TOKEN", "")

# ── Startup diagnostic ────────────────────────────────────────────────────────

def print_env_status():
    """Print which secrets arrived — values never printed, only presence."""
    print("── Env var status ──")
    for name, val in [("NEWS_API_KEY",        NEWS_API_KEY),
                      ("GITHUB_TOKEN",         GITHUB_TOKEN),
                      ("TWITTER_BEARER_TOKEN", TWITTER_BEARER)]:
        print(f"  {name:25s} {'PRESENT' if val else 'MISSING'}")
    print("────────────────────")

# ── False-positive filtering ──────────────────────────────────────────────────

def scrub_false_positives(text: str) -> str:
    """Remove known false-positive compound words so 'water' only remains
    when it genuinely relates to water access."""
    lowered = text.lower()
    for fp in WATER_FALSE_POSITIVES:
        lowered = lowered.replace(fp, "")
    return lowered

def water_relevant(text: str) -> bool:
    """Return True only when the text contains a real water-access term,
    after stripping unrelated compounds like 'watermark' or 'waterfall'."""
    scrubbed = scrub_false_positives(text)
    return any(term in scrubbed for term in WATER_TERMS)

def is_water_access_repo(name: str, desc: str) -> bool:
    """Return False if 'water' only appears inside an unrelated compound word."""
    scrubbed = scrub_false_positives(f"{name} {desc}")
    return "water" in scrubbed

# ── Model resolution ──────────────────────────────────────────────────────────

def resolve_model(text: str) -> tuple:
    """
    Returns (company, model_family, model_variant) for the best match in text.
    Tries all variants first (most specific), then falls back to family aliases.
    Returns (None, None, None) if no match found.
    """
    text_lower = text.lower()

    # Pass 1: exact variant match
    for company, families in MODELS.items():
        for family, variants in families.items():
            for variant, aliases in variants.items():
                if variant.startswith("_"):
                    continue
                if any(alias in text_lower for alias in aliases):
                    return company, family, variant

    # Pass 2: family-level fallback
    for company, families in MODELS.items():
        for family, variants in families.items():
            family_aliases = variants.get("_family_aliases", [])
            if any(alias in text_lower for alias in family_aliases):
                return company, family, None

    return None, None, None

def company_for(text: str):
    company, _, _ = resolve_model(text)
    return company

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)

def save_json(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def article_entry(date, company, model_family, model_variant,
                  source, title, url, snippet):
    """Build a single article entry dict with full model attribution."""
    return {
        "date":          date,
        "company":       company,
        "model_family":  model_family,   # e.g. "claude", "gpt", "gemini" — or null
        "model_variant": model_variant,  # e.g. "claude-sonnet-4-6"       — or null
        "source":        source,
        "title":         title,
        "url":           url,
        "snippet":       snippet[:400] if snippet else "",
    }

def get_with_retry(url, params=None, headers=None, timeout=20, retries=3, backoff=10):
    """GET with exponential backoff on 429 rate-limit responses."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                wait = backoff * (2 ** attempt)
                print(f"    429 rate limit — waiting {wait}s (retry {attempt+1}/{retries})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.HTTPError:
            if attempt == retries - 1:
                raise
            time.sleep(backoff)
    return None

# ── 1. NewsAPI ────────────────────────────────────────────────────────────────

def fetch_newsapi(articles: list):
    if not NEWS_API_KEY:
        print("  [newsapi] skipped — NEWS_API_KEY secret not set")
        return

    company_primary = {
        "openai":    ["OpenAI", "ChatGPT", "GPT-4o"],
        "anthropic": ["Anthropic", "Claude"],
        "google":    ["Google DeepMind", "Gemini"],
        "xai":       ["xAI", "Grok"],
    }
    for company, aliases in company_primary.items():
        query = f"({' OR '.join(aliases)}) AND ({' OR '.join(WATER_TERMS[:4])})"
        params = {
            "q":        query,
            "language": "en",
            "pageSize": 20,
            "from":     THIRTY_DAYS_AGO,
            "sortBy":   "relevancy",
            "apiKey":   NEWS_API_KEY,
        }
        try:
            resp = get_with_retry("https://newsapi.org/v2/everything", params=params)
            for art in resp.json().get("articles", []):
                text = f"{art.get('title','')} {art.get('description','')}"
                if water_relevant(text):
                    _, family, variant = resolve_model(text)
                    articles.append(article_entry(
                        TODAY, company, family, variant, "newsapi",
                        art.get("title", ""),
                        art.get("url", ""),
                        art.get("description", ""),
                    ))
                else:
                    title = art.get("title", "")
                    if "water" in title.lower():
                        print(f"    skipped false positive (newsapi): {title[:80]}")
            time.sleep(1)
        except Exception as e:
            print(f"  [newsapi] {company}: {e}")

# ── 2. GDELT ──────────────────────────────────────────────────────────────────

def fetch_gdelt(articles: list):
    """GDELT DOC 2.0 full-text search — no API key, but strict rate limits.
    Uses 7-day timespan and 5s sleep between company queries to avoid 429s."""
    gdelt_queries = [
        ("openai",    "OpenAI water"),
        ("anthropic", "Anthropic Claude water"),
        ("google",    "Google Gemini water"),
        ("xai",       "xAI Grok water"),
    ]
    for company, query in gdelt_queries:
        params = {
            "query":      query,
            "mode":       "artlist",
            "maxrecords": 20,
            "format":     "json",
            "timespan":   "7d",
        }
        try:
            resp = get_with_retry(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params=params,
                backoff=15,
            )
            for art in resp.json().get("articles", []):
                title = art.get("title", "")
                if water_relevant(title):
                    _, family, variant = resolve_model(title)
                    articles.append(article_entry(
                        TODAY, company, family, variant, "gdelt",
                        title,
                        art.get("url", ""),
                        art.get("seendate", ""),
                    ))
                else:
                    if "water" in title.lower():
                        print(f"    skipped false positive (gdelt): {title[:80]}")
            time.sleep(5)   # GDELT rate-limits hard — do not reduce this
        except Exception as e:
            print(f"  [gdelt] {company}: {e}")

# ── 3. Semantic Scholar ───────────────────────────────────────────────────────

def fetch_semantic_scholar(articles: list):
    """Unauthenticated limit is 100 req/5 min — 5s sleep keeps us well under."""
    scholar_queries = [
        ("openai",    "OpenAI water"),
        ("anthropic", "Anthropic Claude water"),
        ("google",    "Google Gemini water"),
        ("xai",       "xAI water"),
        ("openai",    "GPT-4 water quality"),
        ("anthropic", "Claude LLM irrigation"),
        ("google",    "Gemini water access"),
    ]
    for company, query in scholar_queries:
        params = {
            "query":  query,
            "limit":  10,
            "fields": "title,abstract,year,externalIds,authors",
        }
        try:
            resp = get_with_retry(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params=params,
                backoff=15,
            )
            for paper in resp.json().get("data", []):
                title    = paper.get("title", "")
                abstract = paper.get("abstract", "") or ""
                full_text = f"{title} {abstract}"
                if water_relevant(full_text):
                    # Re-resolve from full text — may be more specific than the query company
                    resolved_co, family, variant = resolve_model(full_text)
                    final_company = resolved_co or company
                    ext = paper.get("externalIds", {})
                    paper_url = (
                        f"https://arxiv.org/abs/{ext['ArXiv']}" if "ArXiv" in ext
                        else f"https://www.semanticscholar.org/paper/{paper.get('paperId','')}"
                    )
                    articles.append(article_entry(
                        TODAY, final_company, family, variant, "semantic_scholar",
                        title, paper_url, abstract[:400],
                    ))
                else:
                    if "water" in title.lower():
                        print(f"    skipped false positive (semantic_scholar): {title[:80]}")
            time.sleep(5)
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
        "Claude water",
        "GPT water access",
        "Gemini water quality",
        "LLM water management",
    ]
    seen_repos = set()
    for q in queries:
        params = {
            "q":        f"{q} pushed:>{THIRTY_DAYS_AGO}",
            "sort":     "updated",
            "order":    "desc",
            "per_page": 10,
        }
        try:
            resp = get_with_retry(
                "https://api.github.com/search/repositories",
                params=params,
                headers=headers,
            )
            for repo in resp.json().get("items", []):
                if repo["full_name"] in seen_repos:
                    continue
                seen_repos.add(repo["full_name"])
                desc      = repo.get("description") or ""
                full_text = f"{repo['full_name']} {desc}"

                if not is_water_access_repo(repo["full_name"], desc):
                    print(f"    skipped false positive (github): {repo['full_name']}")
                    continue

                company, family, variant = resolve_model(full_text)
                articles.append(article_entry(
                    TODAY,
                    company or "general",
                    family,
                    variant,
                    "github",
                    repo["full_name"],
                    repo["html_url"],
                    f"Stars:{repo['stargazers_count']} | {desc}",
                ))
            time.sleep(2)
        except Exception as e:
            print(f"  [github] query '{q}': {e}")

# ── 5. X / Twitter mentions ───────────────────────────────────────────────────

def fetch_twitter_mentions(mentions_data: dict):
    if not TWITTER_BEARER:
        print("  [twitter] skipped — no TWITTER_BEARER_TOKEN")
        return
    headers     = {"Authorization": f"Bearer {TWITTER_BEARER}"}
    water_query = " OR ".join([f'"{t}"' for t in WATER_TERMS[:4]])
    query       = f"@skyberrys ({water_query}) -is:retweet lang:en"
    params      = {
        "query":        query,
        "max_results":  50,
        "tweet.fields": "created_at,author_id,text",
        "expansions":   "author_id",
        "user.fields":  "username",
    }
    try:
        resp = requests.get(
            "https://api.twitter.com/2/tweets/search/recent",
            headers=headers, params=params, timeout=15,
        )
        resp.raise_for_status()
        data  = resp.json()
        users = {u["id"]: u["username"]
                 for u in data.get("includes", {}).get("users", [])}
        for tweet in data.get("data", []):
            text = tweet.get("text", "")
            company, family, variant = resolve_model(text)
            mentions_data["entries"].append({
                "date":          TODAY,
                "tweet_id":      tweet["id"],
                "author":        users.get(tweet.get("author_id", ""), "unknown"),
                "text":          text,
                "company":       company,
                "model_family":  family,
                "model_variant": variant,
                "created_at":    tweet.get("created_at", ""),
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
    print(f"      {len(new_articles)} articles so far\n")

    print("[2/4] GDELT...")
    before = len(new_articles)
    fetch_gdelt(new_articles)
    print(f"      +{len(new_articles) - before} articles\n")

    print("[3/4] Semantic Scholar...")
    before = len(new_articles)
    fetch_semantic_scholar(new_articles)
    print(f"      +{len(new_articles) - before} papers\n")

    print("[4/4] GitHub...")
    before = len(new_articles)
    fetch_github(new_articles)
    print(f"      +{len(new_articles) - before} repos\n")

    # De-duplicate by URL before saving
    existing_urls = {e["url"] for e in articles_data["entries"]}
    deduped = [a for a in new_articles if a["url"] not in existing_urls]
    articles_data["entries"].extend(deduped)

    print("[+] Twitter @skyberrys mentions...")
    fetch_twitter_mentions(mentions_data)

    save_json(DATA_DIR / "articles.json", articles_data)
    save_json(DATA_DIR / "mentions.json", mentions_data)

    # Summary by model
    model_counts = {}
    for a in deduped:
        key = "/".join(filter(None, [
            a.get("company"), a.get("model_family"), a.get("model_variant")
        ])) or "unattributed"
        model_counts[key] = model_counts.get(key, 0) + 1

    print(f"\nDone. +{len(deduped)} new articles saved.")
    print(f"Total articles : {len(articles_data['entries'])}")
    print(f"Total mentions : {len(mentions_data['entries'])}")
    print("\nBreakdown by model:")
    for k, v in sorted(model_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<50s} {v}")

if __name__ == "__main__":
    main()