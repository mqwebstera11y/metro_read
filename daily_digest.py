"""
Daily News Digest v2
- Reads all config from config.yaml (no Python editing needed)
- Fixed double-email bug (single HTML-only email)
- Clean, properly sized HTML email template
"""

import os
import json
import smtplib
import feedparser
import yaml
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
import anthropic

# ─────────────────────────────────────────────
# LOAD CONFIG
# ─────────────────────────────────────────────

with open("config.yaml", "r") as f:
    CONFIG = yaml.safe_load(f)

TOPICS          = CONFIG["topics"]
AUDIENCE        = CONFIG["audience"]
PERSPECTIVE     = CONFIG["perspective"]
COMMENT_STYLE   = CONFIG["comment_style"]
IMPACT_AREAS    = CONFIG["impact_areas"]
CUSTOM_INSTRUCTIONS = CONFIG["custom_instructions"]
RSS_FEEDS       = CONFIG["rss_feeds"]
RECIPIENTS      = CONFIG["recipients"]
SUBJECT_PREFIX  = CONFIG.get("email_subject_prefix", "📰 Daily Digest")

SENDER_EMAIL    = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

MEMORY_DIR = Path("memory")
MEMORY_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────
# STEP 1: FETCH NEWS
# ─────────────────────────────────────────────

def fetch_news(max_articles: int = 20) -> list[dict]:
    print("📡 Fetching news from RSS feeds...")
    matched_articles = []

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:30]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                text = f"{title} {summary}".lower()

                for topic in TOPICS:
                    if topic.lower() in text:
                        matched_articles.append({
                            "title": title,
                            "summary": summary[:500],
                            "link": entry.get("link", ""),
                            "published": entry.get("published", ""),
                            "source": feed.feed.get("title", feed_url),
                            "topic": topic,
                        })
                        break
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

def summarize_with_claude(articles: list[dict]) -> str:
    print("🤖 Sending to Claude for analysis...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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

AUDIENCE: {AUDIENCE}
PERSPECTIVE: {PERSPECTIVE}
COMMENT STYLE: {COMMENT_STYLE}

ADDITIONAL INSTRUCTIONS:
{CUSTOM_INSTRUCTIONS}

Here are today's relevant news articles:
{articles_text}

Produce a structured Daily News Digest with exactly these sections:

## 📰 TODAY'S TOP STORIES
Group by topic. For each topic write a 2-3 sentence summary of what happened today.

## 🕰️ BRIEF HISTORY
For each topic, 3-4 sentences of relevant historical context.

## 💬 ANALYST COMMENTS
Written from the perspective described above. Follow the comment style instructions strictly.

## 🌊 POTENTIAL IMPACT
Assess impact on: {', '.join(IMPACT_AREAS)}.
Rate each: 🔴 High / 🟡 Medium / 🟢 Low with a one-sentence explanation.
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


# ─────────────────────────────────────────────
# STEP 3: SAVE MEMORY
# ─────────────────────────────────────────────

def save_memory(digest: str, articles: list[dict]):
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
    print(f"  💾 Memory saved → {filepath}")


# ─────────────────────────────────────────────
# STEP 4: WEEKLY REFLECTION (Sundays only)
# ─────────────────────────────────────────────

def generate_weekly_reflection() -> str:
    print("📆 Generating weekly reflection...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    week_digests = []
    for i in range(7):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        filepath = MEMORY_DIR / f"{date}.json"
        if filepath.exists():
            with open(filepath) as f:
                week_digests.append(json.load(f))

    if not week_digests:
        return ""

    digests_text = "\n\n---\n\n".join(
        f"**{d['date']}**\n{d['digest'][:800]}..." for d in week_digests
    )

    prompt = f"""You are a weekly analyst reviewing a full week of news summaries.
AUDIENCE: {AUDIENCE}
PERSPECTIVE: {PERSPECTIVE}

Here are this week's daily digests:
{digests_text}

Write a Weekly Review with these sections:

## 🗓️ WEEK IN REVIEW
The 3-5 most important themes or developments this week.

## 📈 TRENDS TO WATCH
What patterns emerged? What should we watch next week?

## 🔁 WHAT CHANGED vs. WHAT STAYED THE SAME
Surprising reversals or persistent stories.

## 💡 KEY TAKEAWAY
One sharp insight to remember from this week.
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


# ─────────────────────────────────────────────
# STEP 5: BUILD HTML EMAIL (fixed font + single email)
# ─────────────────────────────────────────────

def markdown_to_html(text: str) -> str:
    """Convert simple markdown to clean HTML."""
    lines = text.split("\n")
    html_lines = []
    for line in lines:
        if line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("- ") or line.startswith("* "):
            html_lines.append(f"<li>{line[2:]}</li>")
        elif line.startswith("**") and line.endswith("**"):
            html_lines.append(f"<strong>{line[2:-2]}</strong>")
        elif line.strip() == "":
            html_lines.append("<br>")
        else:
            html_lines.append(f"<p>{line}</p>")
    return "\n".join(html_lines)


def build_html_email(digest: str, weekly: str = "") -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    digest_html = markdown_to_html(digest)
    weekly_section = ""
    if weekly:
        weekly_html = markdown_to_html(weekly)
        weekly_section = f"""
        <div style="margin-top:40px; padding-top:30px; border-top:2px solid #e2e8f0;">
            <h2 style="color:#1a202c; font-size:18px;">🗓️ Weekly Reflection</h2>
            {weekly_html}
        </div>
        """

    return f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0; padding:0; background-color:#f7fafc; font-family: Georgia, 'Times New Roman', serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f7fafc; padding:30px 0;">
    <tr>
      <td align="center">
        <table width="620" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:8px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,0.08);">

          <!-- HEADER -->
          <tr>
            <td style="background:#1a202c; padding:28px 36px;">
              <p style="margin:0; color:#a0aec0; font-size:12px; font-family:Arial,sans-serif; letter-spacing:1px; text-transform:uppercase;">Daily Intelligence Digest</p>
              <h1 style="margin:6px 0 0 0; color:#ffffff; font-size:22px; font-family:Arial,sans-serif; font-weight:600;">{today}</h1>
            </td>
          </tr>

          <!-- TOPICS BADGE ROW -->
          <tr>
            <td style="padding:16px 36px; background:#f0f4f8; border-bottom:1px solid #e2e8f0;">
              <p style="margin:0; font-size:12px; font-family:Arial,sans-serif; color:#4a5568;">
                📌 Tracking: <strong>{" &nbsp;·&nbsp; ".join(TOPICS)}</strong>
              </p>
            </td>
          </tr>

          <!-- MAIN CONTENT -->
          <tr>
            <td style="padding:32px 36px; color:#2d3748; font-size:15px; line-height:1.7;">
              <style>
                h1 {{ font-size:20px; color:#1a202c; margin:28px 0 10px 0; font-family:Arial,sans-serif; }}
                h2 {{ font-size:16px; color:#2b6cb0; margin:24px 0 8px 0; font-family:Arial,sans-serif; border-bottom:1px solid #bee3f8; padding-bottom:4px; }}
                p  {{ font-size:15px; margin:6px 0; color:#2d3748; }}
                li {{ font-size:15px; margin:4px 0 4px 20px; color:#2d3748; }}
                strong {{ color:#1a202c; }}
              </style>
              {digest_html}
              {weekly_section}
            </td>
          </tr>

          <!-- FOOTER -->
          <tr>
            <td style="padding:20px 36px; background:#f7fafc; border-top:1px solid #e2e8f0;">
              <p style="margin:0; font-size:11px; color:#a0aec0; font-family:Arial,sans-serif; text-align:center;">
                Generated by your Daily News Digest · Powered by Claude AI · Delivered automatically at 7AM EST
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


# ─────────────────────────────────────────────
# STEP 6: SEND EMAIL (single HTML email, fixed)
# ─────────────────────────────────────────────

def send_email(subject: str, html_body: str):
    print(f"📧 Sending to {len(RECIPIENTS)} recipient(s)...")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = ", ".join(RECIPIENTS)

    # HTML only — no plain text part (fixes double email bug)
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENTS, msg.as_string())
        print("  ✅ Email sent!")
    except Exception as e:
        print(f"  ⚠️  Email failed: {e}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run_daily():
    print(f"\n🌅 Daily News Digest v2 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    articles = fetch_news()
    if not articles:
        print("⚠️  No articles matched your topics today.")
        return

    digest = summarize_with_claude(articles)
    save_memory(digest, articles)

    weekly = ""
    if datetime.now().weekday() == 6:  # Sunday
        weekly = generate_weekly_reflection()

    today = datetime.now().strftime("%A, %B %d")
    subject = f"{SUBJECT_PREFIX} — {today}"
    if weekly:
        subject += " · 📆 + Weekly Review"

    html = build_html_email(digest, weekly)
    send_email(subject, html)
    print("\n✅ Done!")


if __name__ == "__main__":
    run_daily()
