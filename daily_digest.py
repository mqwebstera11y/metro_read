"""
Daily News Digest - Starter Code
Fetches news by topic, summarizes with Claude AI, emails results, and saves memory.
"""

import os
import json
import smtplib
import feedparser
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
import anthropic

# ─────────────────────────────────────────────
# CONFIGURATION — edit these to customize
# ─────────────────────────────────────────────

TOPICS = [
    "artificial intelligence",
    "Federal Reserve interest rates",
    "climate change policy",
]

CONSTRAINTS = {
    "perspective": "neutral, analytical",
    "audience": "busy professional",
    "impact_areas": ["financial markets", "technology industry", "daily life"],
    "comment_style": "concise, thought-provoking",
}

RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/topNews",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "https://feeds.a.dj.com/rss/RSSWorldNews.xml",  # WSJ
]

# Email config (set as env vars or fill in directly for testing)
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "your_email@gmail.com")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "your_app_password")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "recipient@example.com")

# Anthropic API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "your_api_key_here")

# Memory folder
MEMORY_DIR = Path("memory")
MEMORY_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────
# STEP 1: FETCH NEWS
# ─────────────────────────────────────────────

def fetch_news(topics: list[str], max_articles: int = 20) -> list[dict]:
    """Fetch articles from RSS feeds and filter by topics."""
    print("📡 Fetching news from RSS feeds...")
    matched_articles = []

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:30]:  # check top 30 per feed
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                text = f"{title} {summary}".lower()

                for topic in topics:
                    if topic.lower() in text:
                        matched_articles.append({
                            "title": title,
                            "summary": summary[:500],
                            "link": entry.get("link", ""),
                            "published": entry.get("published", ""),
                            "source": feed.feed.get("title", feed_url),
                            "topic": topic,
                        })
                        break  # avoid duplicating if matches multiple topics

        except Exception as e:
            print(f"  ⚠️  Could not fetch {feed_url}: {e}")

    # Deduplicate by title
    seen = set()
    unique = []
    for a in matched_articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            unique.append(a)

    print(f"  ✅ Found {len(unique)} relevant articles")
    return unique[:max_articles]


# ─────────────────────────────────────────────
# STEP 2: SUMMARIZE WITH CLAUDE
# ─────────────────────────────────────────────

def summarize_with_claude(articles: list[dict], constraints: dict) -> str:
    """Send articles to Claude API for summarization, history, and impact."""
    print("🤖 Sending to Claude for analysis...")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Format articles into prompt
    articles_text = ""
    for i, a in enumerate(articles, 1):
        articles_text += f"""
Article {i}: [{a['source']}] — Topic: {a['topic']}
Title: {a['title']}
Summary: {a['summary']}
URL: {a['link']}
"""

    today = datetime.now().strftime("%A, %B %d, %Y")

    prompt = f"""You are a daily news analyst. Today is {today}.

Here are today's relevant news articles:
{articles_text}

Please produce a structured Daily News Digest with these sections:

## 📰 TODAY'S TOP STORIES
Group articles by topic. For each topic, write a 2–3 sentence summary of what happened today.

## 🕰️ BRIEF HISTORY
For each topic, provide 3–4 sentences of relevant historical context — what led to this moment?

## 💬 ANALYST COMMENTS
Write from the perspective of a {constraints['perspective']} analyst writing for a {constraints['audience']}.
Be {constraints['comment_style']}.

## 🌊 POTENTIAL IMPACT
Assess the potential impact on: {', '.join(constraints['impact_areas'])}.
Rate each impact as: 🔴 High / 🟡 Medium / 🟢 Low and explain briefly.

Keep the entire digest readable in under 5 minutes. Use clear headers and bullet points.
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",  # cheapest model — swap for sonnet for higher quality
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


# ─────────────────────────────────────────────
# STEP 3: SAVE MEMORY
# ─────────────────────────────────────────────

def save_memory(digest: str, articles: list[dict]) -> str:
    """Save today's digest to a JSON file for weekly review."""
    today = datetime.now().strftime("%Y-%m-%d")
    filepath = MEMORY_DIR / f"{today}.json"

    data = {
        "date": today,
        "articles_count": len(articles),
        "topics": list(set(a["topic"] for a in articles)),
        "digest": digest,
        "article_titles": [a["title"] for a in articles],
    }

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    print(f"  💾 Memory saved to {filepath}")
    return str(filepath)


# ─────────────────────────────────────────────
# STEP 4: WEEKLY REFLECTION (runs on Sundays)
# ─────────────────────────────────────────────

def generate_weekly_reflection() -> str:
    """Look back at the week's digests and generate a reflection."""
    print("📆 Generating weekly reflection...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Load last 7 days of memory
    week_digests = []
    for i in range(7):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        filepath = MEMORY_DIR / f"{date}.json"
        if filepath.exists():
            with open(filepath) as f:
                week_digests.append(json.load(f))

    if not week_digests:
        return "No weekly data found."

    digests_text = "\n\n---\n\n".join(
        f"**{d['date']}** ({d['articles_count']} articles)\n{d['digest'][:800]}..."
        for d in week_digests
    )

    prompt = f"""You are a weekly analyst reviewing a full week of news summaries.

Here are the daily digests from this week:

{digests_text}

Please write a **Weekly Review** covering:

## 🗓️ WEEK IN REVIEW
What were the 3–5 most important themes or developments this week?

## 📈 TRENDS TO WATCH
What patterns emerged? What should we watch next week?

## 🔁 WHAT CHANGED vs. WHAT STAYED THE SAME
Highlight any surprising reversals or persistent stories.

## 💡 KEY TAKEAWAY
One sharp insight a busy professional should remember from this week.

Keep it concise and insightful.
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


# ─────────────────────────────────────────────
# STEP 5: SEND EMAIL
# ─────────────────────────────────────────────

def send_email(subject: str, body: str):
    """Send the digest via Gmail SMTP."""
    print(f"📧 Sending email to {RECIPIENT_EMAIL}...")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL

    # Plain text version
    msg.attach(MIMEText(body, "plain"))

    # Simple HTML version
    html_body = body.replace("\n", "<br>").replace("## ", "<h2>").replace("# ", "<h1>")
    msg.attach(MIMEText(f"<html><body style='font-family:sans-serif;max-width:700px;margin:auto;padding:20px'>{html_body}</body></html>", "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        print("  ✅ Email sent!")
    except Exception as e:
        print(f"  ⚠️  Email failed (check credentials): {e}")
        print("\n--- EMAIL CONTENT PREVIEW ---")
        print(body[:1000])


# ─────────────────────────────────────────────
# MAIN RUNNER
# ─────────────────────────────────────────────

def run_daily():
    print(f"\n🌅 Daily News Digest — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    articles = fetch_news(TOPICS)

    if not articles:
        print("⚠️  No articles found. Check RSS feeds or topics.")
        return

    digest = summarize_with_claude(articles, CONSTRAINTS)
    save_memory(digest, articles)

    today = datetime.now().strftime("%A, %B %d")
    subject = f"📰 Daily Digest — {today}"

    # Add weekly reflection on Sundays
    if datetime.now().weekday() == 6:  # Sunday = 6
        print("\n📆 It's Sunday — adding weekly reflection...")
        weekly = generate_weekly_reflection()
        full_email = f"{digest}\n\n{'='*50}\n\n{weekly}"
    else:
        full_email = digest

    send_email(subject, full_email)
    print("\n✅ Done!")


if __name__ == "__main__":
    run_daily()
