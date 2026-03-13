"""
Daily News Digest v3
- Supports rich topic config (name + description + keywords)
- Uses NewsAPI instead of RSS (reliable from GitHub cloud servers)
- Clean HTML email, single send, weekly reflection
"""

import os
import json
import smtplib
import yaml
import requests
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

# Support rich topic format: {name, description, keywords}
RAW_TOPICS      = CONFIG["topics"]
AUDIENCE        = CONFIG["audience"]
PERSPECTIVE     = CONFIG["perspective"]
COMMENT_STYLE   = CONFIG["comment_style"]
IMPACT_AREAS    = CONFIG["impact_areas"]
CUSTOM_INSTRUCTIONS = CONFIG["custom_instructions"]
RECIPIENTS      = CONFIG["recipients"]
SUBJECT_PREFIX  = CONFIG.get("email_subject_prefix", "📰 Daily Digest")

SENDER_EMAIL      = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD   = os.getenv("SENDER_PASSWORD")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
NEWS_API_KEY      = os.getenv("NEWS_API_KEY")

MEMORY_DIR = Path("memory")
MEMORY_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────
# HELPERS: Parse topic config flexibly
# ─────────────────────────────────────────────

def get_topic_name(topic) -> str:
    """Works whether topic is a string or a dict with 'name'."""
    if isinstance(topic, str):
        return topic
    return topic.get("name", "General")

def get_topic_keywords(topic) -> list[str]:
    """Returns list of keywords to search for this topic."""
    if isinstance(topic, str):
        return [topic]
    return topic.get("keywords", [topic.get("name", "")])

def get_topic_description(topic) -> str:
    """Returns the rich description for Claude's context."""
    if isinstance(topic, str):
        return topic
    return topic.get("description", topic.get("name", ""))


# ─────────────────────────────────────────────
# STEP 1: FETCH NEWS via NewsAPI
# ─────────────────────────────────────────────

def fetch_news(max_per_topic: int = 5) -> list[dict]:
    """Fetch articles from NewsAPI, one search per topic using its keywords."""
    print("📡 Fetching news via NewsAPI...")
    all_articles = []
    seen_titles = set()

    for topic in RAW_TOPICS:
        topic_name = get_topic_name(topic)
        keywords   = get_topic_keywords(topic)

        # Use first 3 keywords joined with OR for the query
        query = " OR ".join(f'"{kw}"' for kw in keywords[:3])

        try:
            response = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": max_per_topic,
                    "from": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                },
                headers={"X-Api-Key": NEWS_API_KEY},
                timeout=10,
            )
            data = response.json()

            if data.get("status") != "ok":
                print(f"  ⚠️  NewsAPI error for '{topic_name}': {data.get('message')}")
                continue

            for article in data.get("articles", []):
                title = article.get("title", "") or ""
                if title in seen_titles or title == "[Removed]":
                    continue
                seen_titles.add(title)
                all_articles.append({
                    "title": title,
                    "summary": (article.get("description") or "")[:500],
                    "link": article.get("url", ""),
                    "published": article.get("publishedAt", ""),
                    "source": article.get("source", {}).get("name", "Unknown"),
                    "topic_name": topic_name,
                    "topic_description": get_topic_description(topic),
                })

        except Exception as e:
            print(f"  ⚠️  Failed to fetch '{topic_name}': {e}")

    print(f"  ✅ Found {len(all_articles)} relevant articles across {len(RAW_TOPICS)} topics")
    return all_articles


# ─────────────────────────────────────────────
# STEP 2: SUMMARIZE WITH CLAUDE
# ─────────────────────────────────────────────

def summarize_with_claude(articles: list[dict]) -> str:
    print("🤖 Sending to Claude for analysis...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Build topic context block so Claude understands each topic deeply
    topic_context = ""
    for topic in RAW_TOPICS:
        topic_context += f"\n- **{get_topic_name(topic)}**: {get_topic_description(topic)}"

    # Build articles block grouped by topic
    articles_text = ""
    for i, a in enumerate(articles, 1):
        articles_text += f"""
Article {i}: [{a['source']}] — Topic: {a['topic_name']}
Title: {a['title']}
Summary: {a['summary']}
Published: {a['published']}
URL: {a['link']}
"""

    today = datetime.now().strftime("%A, %B %d, %Y")

    prompt = f"""You are a daily news analyst. Today is {today}.

AUDIENCE:
{AUDIENCE}

PERSPECTIVE:
{PERSPECTIVE}

COMMENT STYLE:
{COMMENT_STYLE}

TOPIC DEFINITIONS (use these to frame your analysis):
{topic_context}

BEHAVIORAL INSTRUCTIONS:
{CUSTOM_INSTRUCTIONS}

HERE ARE TODAY'S ARTICLES:
{articles_text}

Produce a structured Daily News Digest with exactly these four sections:

## 📰 TODAY'S TOP STORIES
Group articles by topic name. For each topic write a 2–3 sentence summary of what happened today.
Use the topic's definition above to frame the significance of each story.

## 🕰️ BRIEF HISTORY
For each topic, provide 3–4 sentences of genuinely useful historical context.
Connect today's news to longer civilizational or structural trends where relevant.

## 💬 ANALYST COMMENTS
Apply the perspective and comment style defined above. 
Connect findings to the philosophical anchor in the instructions.
One sharp insight per topic — no filler.

## 🌊 POTENTIAL IMPACT
Assess impact on: {', '.join(IMPACT_AREAS)}.
Rate each: 🔴 High / 🟡 Medium / 🟢 Low with one precise sentence of explanation.
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=3000,
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
        "topics": list(set(a["topic_name"] for a in articles)),
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

This digest tracks these themes: {", ".join(get_topic_name(t) for t in RAW_TOPICS)}

Here are this week's daily digests:
{digests_text}

Write a concise Weekly Review:

## 🗓️ WEEK IN REVIEW
The 3–5 most important developments this week across all topics.

## 📈 TRENDS TO WATCH
What patterns emerged? What should we watch next week?

## 🔁 WHAT CHANGED vs. WHAT STAYED THE SAME

## 💡 KEY TAKEAWAY
One sharp insight connecting this week's news to the civilizational question
of human identity and dignity in the age of AI.
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


# ─────────────────────────────────────────────
# STEP 5: BUILD HTML EMAIL
# ─────────────────────────────────────────────

def markdown_to_html(text: str) -> str:
    lines = text.split("\n")
    html_lines = []
    in_list = False
    for line in lines:
        if line.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f'<h2 style="color:#2b6cb0;font-size:16px;font-family:Arial,sans-serif;margin:24px 0 8px 0;border-bottom:1px solid #bee3f8;padding-bottom:4px;">{line[3:]}</h2>')
        elif line.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f'<h1 style="color:#1a202c;font-size:20px;font-family:Arial,sans-serif;margin:28px 0 10px 0;">{line[2:]}</h1>')
        elif line.startswith("- ") or line.startswith("* "):
            if not in_list:
                html_lines.append('<ul style="padding-left:20px;margin:6px 0;">')
                in_list = True
            html_lines.append(f'<li style="font-size:15px;color:#2d3748;margin:4px 0;">{line[2:]}</li>')
        elif line.strip() == "":
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<br>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            # Bold: **text**
            import re
            line = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', line)
            html_lines.append(f'<p style="font-size:15px;color:#2d3748;margin:6px 0;line-height:1.7;">{line}</p>')
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def build_html_email(digest: str, weekly: str = "") -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    topic_names = " &nbsp;·&nbsp; ".join(get_topic_name(t) for t in RAW_TOPICS)
    digest_html = markdown_to_html(digest)

    weekly_section = ""
    if weekly:
        weekly_html = markdown_to_html(weekly)
        weekly_section = f"""
        <tr>
          <td style="padding:32px 36px; border-top:2px solid #e2e8f0;">
            <h2 style="color:#1a202c;font-size:18px;font-family:Arial,sans-serif;margin:0 0 16px 0;">🗓️ Weekly Reflection</h2>
            {weekly_html}
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f7fafc;font-family:Georgia,'Times New Roman',serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f7fafc;padding:30px 0;">
  <tr><td align="center">
    <table width="640" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

      <!-- HEADER -->
      <tr>
        <td style="background:#1a202c;padding:28px 36px;">
          <p style="margin:0;color:#a0aec0;font-size:11px;font-family:Arial,sans-serif;letter-spacing:2px;text-transform:uppercase;">{SUBJECT_PREFIX}</p>
          <h1 style="margin:6px 0 0 0;color:#ffffff;font-size:22px;font-family:Arial,sans-serif;font-weight:600;">{today}</h1>
        </td>
      </tr>

      <!-- TOPICS BAR -->
      <tr>
        <td style="padding:14px 36px;background:#edf2f7;border-bottom:1px solid #e2e8f0;">
          <p style="margin:0;font-size:12px;font-family:Arial,sans-serif;color:#4a5568;">
            📌 <strong>Tracking:</strong> {topic_names}
          </p>
        </td>
      </tr>

      <!-- MAIN CONTENT -->
      <tr>
        <td style="padding:32px 36px;">
          {digest_html}
        </td>
      </tr>

      <!-- WEEKLY (if Sunday) -->
      {weekly_section}

      <!-- FOOTER -->
      <tr>
        <td style="padding:20px 36px;background:#f7fafc;border-top:1px solid #e2e8f0;">
          <p style="margin:0;font-size:11px;color:#a0aec0;font-family:Arial,sans-serif;text-align:center;">
            {SUBJECT_PREFIX} · Powered by Claude AI · Delivered automatically at 7AM EST
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body></html>"""


# ─────────────────────────────────────────────
# STEP 6: SEND EMAIL
# ─────────────────────────────────────────────

def send_email(subject: str, html_body: str):
    print(f"📧 Sending to {len(RECIPIENTS)} recipient(s)...")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = ", ".join(RECIPIENTS)
    msg.attach(MIMEText(html_body, "html"))  # HTML only — no plain text (fixes double email)

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
    print(f"\n🌅 Daily News Digest v3 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    if not NEWS_API_KEY:
        print("❌ NEWS_API_KEY not set. Add it to GitHub Secrets.")
        return

    articles = fetch_news()
    if not articles:
        print("⚠️  No articles found. Check NEWS_API_KEY or topic keywords.")
        return

    digest = summarize_with_claude(articles)
    save_memory(digest, articles)

    weekly = ""
    if datetime.now().weekday() == 6:  # Sunday
        weekly = generate_weekly_reflection()

    today = datetime.now().strftime("%A, %B %d")
    subject = f"{SUBJECT_PREFIX} — {today}"
    if weekly:
        subject += " · 📆 Weekly Review"

    html = build_html_email(digest, weekly)
    send_email(subject, html)
    print("\n✅ Done!")


if __name__ == "__main__":
    run_daily()
