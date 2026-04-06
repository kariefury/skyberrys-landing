"""
ingest_mention.py  —  Manually add a water-access mention or field report.

Use this when someone DMs @skyberrys on X with a water access story,
or when you want to record a field report directly.

Usage:
  python scripts/ingest_mention.py

Or non-interactively (for piping / scripting):
  python scripts/ingest_mention.py \\
    --author "jane_doe" \\
    --text "Used ChatGPT to model contamination spread for 3 villages in Kenya. 1200 people now have cleaner water." \\
    --company openai \\
    --tweet_id "1234567890"
"""

import json
import datetime
import argparse
from pathlib import Path

DATA_DIR  = Path(__file__).parent.parent / "data"
TODAY     = datetime.date.today().isoformat()

COMPANIES = ["openai", "anthropic", "google", "xai", "general"]

def load_json(path):
    with open(path) as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def ingest(author: str, text: str, company: str,
           tweet_id: str = "", date: str = TODAY):
    mentions_data = load_json(DATA_DIR / "mentions.json")
    entry = {
        "date":       date,
        "tweet_id":   tweet_id,
        "author":     author,
        "text":       text,
        "company":    company,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "manual":     True,
    }
    mentions_data["entries"].append(entry)
    save_json(DATA_DIR / "mentions.json", mentions_data)
    print(f"\nSaved mention from @{author} for company '{company}'.")
    print(f"Run `python scripts/score.py` to update today's scores.")

def interactive():
    print("=== Add a water-access mention ===\n")
    author   = input("Twitter/X handle (without @): ").strip()
    text     = input("Message text: ").strip()
    print(f"Companies: {', '.join(COMPANIES)}")
    company  = input("Which company does this relate to? ").strip().lower()
    if company not in COMPANIES:
        print(f"Unknown company '{company}', defaulting to 'general'.")
        company = "general"
    tweet_id = input("Tweet ID (optional, press Enter to skip): ").strip()
    date_in  = input(f"Date (YYYY-MM-DD, default today={TODAY}): ").strip()
    date     = date_in if date_in else TODAY
    ingest(author, text, company, tweet_id, date)

def main():
    parser = argparse.ArgumentParser(description="Manually ingest a water-access mention.")
    parser.add_argument("--author",   default="")
    parser.add_argument("--text",     default="")
    parser.add_argument("--company",  default="general")
    parser.add_argument("--tweet_id", default="")
    parser.add_argument("--date",     default=TODAY)
    args = parser.parse_args()

    if args.author and args.text:
        ingest(args.author, args.text, args.company, args.tweet_id, args.date)
    else:
        interactive()

if __name__ == "__main__":
    main()
