"""
Daily News Digest v4
- One top story per topic (past 24 hours)
- Each story: Who did what + Source comments + AI interpretation + Plain explanation
- Final section: AI synthesis on human dignity & labor
- Cleaner, more readable language
"""

import os
import json
import re
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

RAW_TOPICS          = CONFIG["topics"]
AUDIENCE            = CONFIG["audience"]
PERSPECTIVE         = CONFIG["perspective"]
COMMENT_STYLE       = CONFIG["comment_style"]
IMPACT_AREAS        = CONFIG["impact_areas"]
CUSTOM_INSTRUCTIONS = CONFIG["custom_instructions"]
RECIPIENTS          = CONFIG["recipients"]
SUBJECT_PREFIX      = CONFIG.get("email_subject_prefix", "📰 Daily Digest")

SENDER_EMAIL        = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD     = os.getenv("SENDER_PASSWORD")
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY")
NEWS_API_KEY        = os.getenv("NEWS_API_KEY")

MEMORY_DIR = Path("memory")
MEMORY_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_topic_name(topic) -> str:
    return topic if isinstance(topic, str) else topic.get("name", "General")

def get_topic_keywords(topic) -> list:
    return [topic] if isinstance(topic, str) else topic.get("keywords", [topic.get("name", "")])

def get_topic_description(topic) -> str:
    return topic if isinstance(topic, str) else topic.get("description", topic.get("name", ""))


# ─────────────────────────────────────────────
# STEP 1: FETCH ONE TOP STORY PER TOPIC
# ─────────────────────────────────────────────

def fetch_top_story_per_topic() -> list:
    """Fetch the single most recent relevant article for each topic."""
    print("📡 Fetching top story per topic via NewsAPI...")
    top_stories = []

    for topic in RAW_TOPICS:
        topic_name = get_topic_name(topic)
        keywords   = get_topic_keywords(topic)

        # Build simple query — first two words of each keyword, no quotes
        simple_terms = []
        for kw in keywords[:4]:
            words = kw.strip().split()[:2]
            simple_terms.append(" ".join(words))
        query = " OR ".join(simple_terms)

        found = False
        for hours_back in [24, 72]:
            if found:
                break
            try:
                response = requests.get(
                    "https://newsapi.org/v2/everything",
                    params={
                        "q": query,
                        "language": "en",
                        "sortBy": "publishedAt",
                        "pageSize": 5,
                        "from": (datetime.now() - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%S"),
                    },
                    headers={"X-Api-Key": NEWS_API_KEY},
                    timeout=10,
                )
                data = response.json()

                if data.get("status") != "ok":
                    print(f"  NewsAPI error for {topic_name}: {data.get('message')}")
                    break

                articles = [
                    a for a in data.get("articles", [])
                    if a.get("title") and a.get("title") != "[Removed]"
                    and a.get("description")
                ]

                if articles:
                    best = articles[0]
                    top_stories.append({
                        "topic_name":        topic_name,
                        "topic_description": get_topic_description(topic),
                        "title":             best.get("title", ""),
                        "description":       (best.get("description") or "")[:600],
                        "content":           (best.get("content") or "")[:800],
                        "source":            best.get("source", {}).get("name", "Unknown Source"),
                        "author":            best.get("author", ""),
                        "url":               best.get("url", ""),
                        "published":         best.get("publishedAt", ""),
                    })
                    label = "24h" if hours_back == 24 else "3d fallback"
                    print(f"  OK [{topic_name}] ({label}) -> {best.get('title', '')[:55]}...")
                    found = True
                else:
                    if hours_back == 24:
                        print(f"  [{topic_name}] Nothing in 24h, trying 3 days...")
                    else:
                        print(f"  [{topic_name}] No articles found even in 3 days")

            except Exception as e:
                print(f"  Failed to fetch {topic_name}: {e}")
                break

    print(f"\n  Total: {len(top_stories)} stories across {len(RAW_TOPICS)} topics")
    return top_stories

def analyze_story(story: dict, client) -> dict:
    """Ask Claude to analyze a single story in the required structure."""

    prompt = f"""You are a news analyst writing for this audience:
"{AUDIENCE}"

Analyze this news story and respond in exactly this JSON structure.
Use clear, plain English — short sentences, no jargon, no buzzwords.
Even though the audience is sophisticated, write so anyone can understand it.

STORY:
Topic: {story['topic_name']}
Topic context: {story['topic_description']}
Source: {story['source']}
Title: {story['title']}
Description: {story['description']}
Content excerpt: {story['content']}

Respond ONLY with valid JSON, no markdown, no extra text:
{{
  "who_did_what": "2-3 plain sentences. State clearly: who are the key actors, what did they do or say, when and where. No interpretation yet.",
  "source_comments": "2-3 sentences summarizing how {story['source']} framed or commented on this story. What angle did they take? What did they emphasize or downplay?",
  "ai_interpretation": "2-3 sentences. What does this actually mean? Cut through the noise. What is the real significance of this development for the topic: {story['topic_name']}?",
  "plain_explanation": "2-3 sentences. Explain this story as if to a smart person who knows nothing about this topic. What is the bigger picture? Why does it matter?"
}}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'^```\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "who_did_what":      "Could not parse analysis.",
            "source_comments":   "Could not parse analysis.",
            "ai_interpretation": "Could not parse analysis.",
            "plain_explanation": "Could not parse analysis.",
        }


def generate_synthesis(stories: list, client) -> str:
    """Final section: AI synthesis across all stories on human dignity theme."""

    stories_summary = "\n\n".join(
        f"Topic: {s['topic_name']}\nHeadline: {s['title']}\nSummary: {s['description']}"
        for s in stories
    )

    prompt = f"""You are a thoughtful analyst. Below are today's top stories across several topics.

{stories_summary}

Write a synthesis section titled "What This Means for Human Dignity & Labor".

Rules:
- Plain English. Short sentences. No jargon.
- 3-4 paragraphs maximum.
- Address this central question directly:
  Taken together, what do today's stories tell us about the shift away from
  performance-based identity toward intrinsic human worth — as AI progressively
  replaces human functional contribution?
- Be honest. If today's news accelerates this shift, say so.
  If it slows it, say that. If it is ambiguous, explain why.
- End with one concrete, actionable observation for the reader.
- Do not repeat story details. Synthesize meaning, not facts."""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text.strip()


def analyze_all_stories(stories: list) -> tuple:
    """Run Claude analysis on each story + generate synthesis."""
    print("🤖 Analyzing stories with Claude...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    analyzed = []
    for story in stories:
        print(f"  → Analyzing: {story['topic_name']}...")
        analysis = analyze_story(story, client)
        analyzed.append({**story, "analysis": analysis})

    print("  → Generating synthesis...")
    synthesis = generate_synthesis(stories, client)

    return analyzed, synthesis


# ─────────────────────────────────────────────
# STEP 3: SAVE MEMORY
# ─────────────────────────────────────────────

def save_memory(analyzed_stories: list, synthesis: str):
    today = datetime.now().strftime("%Y-%m-%d")
    filepath = MEMORY_DIR / f"{today}.json"
    data = {
        "date":    today,
        "stories": [
            {"topic": s["topic_name"], "title": s["title"], "url": s["url"]}
            for s in analyzed_stories
        ],
        "synthesis": synthesis,
    }
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  💾 Memory saved → {filepath}")


# ─────────────────────────────────────────────
# STEP 4: WEEKLY REFLECTION (Sundays)
# ─────────────────────────────────────────────

def generate_weekly_reflection() -> str:
    print("📆 Generating weekly reflection...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    week_data = []
    for i in range(7):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        filepath = MEMORY_DIR / f"{date}.json"
        if filepath.exists():
            with open(filepath) as f:
                week_data.append(json.load(f))

    if not week_data:
        return ""

    week_text = "\n\n".join(
        f"{d['date']}:\n" + "\n".join(f"- [{s['topic']}] {s['title']}" for s in d.get("stories", []))
        for d in week_data
    )

    message = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY).messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": f"""Review this week's top stories and write a short weekly reflection.
Plain English. 3-4 paragraphs. Focus on the week's arc — what changed, what stayed the same,
and what it means for human dignity and labor in the age of AI.

THIS WEEK'S STORIES:
{week_text}"""}]
    )
    return message.content[0].text.strip()


# ─────────────────────────────────────────────
# STEP 5: BUILD HTML EMAIL
# ─────────────────────────────────────────────

def format_published(iso_str: str) -> str:
    try:
        dt = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%B %d, %Y · %H:%M UTC")
    except:
        return iso_str


def build_story_card(story: dict, index: int) -> str:
    a = story["analysis"]
    pub = format_published(story["published"])
    author_line = f" · {story['author']}" if story.get("author") else ""

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:32px;">
      <tr>
        <td style="background:#f0f4f8;border-left:4px solid #2b6cb0;padding:6px 14px;border-radius:0 4px 4px 0;">
          <p style="margin:0;font-size:11px;font-family:Arial,sans-serif;color:#2b6cb0;font-weight:700;text-transform:uppercase;letter-spacing:1px;">
            Story {index} &nbsp;·&nbsp; {story['topic_name']}
          </p>
        </td>
      </tr>
      <tr><td style="padding-top:12px;">
        <h2 style="margin:0 0 4px 0;font-size:17px;font-family:Arial,sans-serif;color:#1a202c;line-height:1.4;">
          <a href="{story['url']}" style="color:#1a202c;text-decoration:none;">{story['title']}</a>
        </h2>
        <p style="margin:0 0 16px 0;font-size:11px;font-family:Arial,sans-serif;color:#a0aec0;">
          {story['source']}{author_line} &nbsp;·&nbsp; {pub}
          &nbsp;·&nbsp; <a href="{story['url']}" style="color:#2b6cb0;">Read original →</a>
        </p>
      </td></tr>

      <tr><td style="padding-bottom:10px;">
        <p style="margin:0 0 4px 0;font-size:12px;font-family:Arial,sans-serif;color:#718096;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">🔍 What Happened</p>
        <p style="margin:0;font-size:15px;font-family:Georgia,serif;color:#2d3748;line-height:1.7;">{a.get('who_did_what','')}</p>
      </td></tr>

      <tr><td style="padding-bottom:10px;">
        <p style="margin:0 0 4px 0;font-size:12px;font-family:Arial,sans-serif;color:#718096;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">📰 What the Source Said</p>
        <p style="margin:0;font-size:15px;font-family:Georgia,serif;color:#2d3748;line-height:1.7;">{a.get('source_comments','')}</p>
      </td></tr>

      <tr><td style="padding-bottom:10px;">
        <p style="margin:0 0 4px 0;font-size:12px;font-family:Arial,sans-serif;color:#718096;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">🤖 AI Interpretation</p>
        <p style="margin:0;font-size:15px;font-family:Georgia,serif;color:#2d3748;line-height:1.7;">{a.get('ai_interpretation','')}</p>
      </td></tr>

      <tr><td style="padding-bottom:4px;">
        <p style="margin:0 0 4px 0;font-size:12px;font-family:Arial,sans-serif;color:#718096;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">💡 Plain English</p>
        <p style="margin:0;font-size:15px;font-family:Georgia,serif;color:#2d3748;line-height:1.7;">{a.get('plain_explanation','')}</p>
      </td></tr>
    </table>
    <hr style="border:none;border-top:1px solid #e2e8f0;margin:0 0 32px 0;">
"""


def build_html_email(analyzed_stories: list, synthesis: str, weekly: str = "") -> str:
    today      = datetime.now().strftime("%A, %B %d, %Y")
    topic_names = " &nbsp;·&nbsp; ".join(get_topic_name(t) for t in RAW_TOPICS)
    story_count = len(analyzed_stories)

    # Build story cards
    story_cards = ""
    for i, story in enumerate(analyzed_stories, 1):
        story_cards += build_story_card(story, i)

    # Synthesis section
    synthesis_paragraphs = "".join(
        f'<p style="margin:0 0 14px 0;font-size:15px;font-family:Georgia,serif;color:#2d3748;line-height:1.7;">{p.strip()}</p>'
        for p in synthesis.split("\n\n") if p.strip()
    )

    # Weekly section
    weekly_section = ""
    if weekly:
        weekly_paragraphs = "".join(
            f'<p style="margin:0 0 14px 0;font-size:15px;font-family:Georgia,serif;color:#2d3748;line-height:1.7;">{p.strip()}</p>'
            for p in weekly.split("\n\n") if p.strip()
        )
        weekly_section = f"""
      <tr>
        <td style="padding:32px 36px;border-top:2px solid #e2e8f0;">
          <p style="margin:0 0 4px 0;font-size:11px;font-family:Arial,sans-serif;color:#a0aec0;letter-spacing:2px;text-transform:uppercase;">Every Sunday</p>
          <h2 style="margin:0 0 20px 0;font-size:18px;font-family:Arial,sans-serif;color:#1a202c;">🗓️ Week in Review</h2>
          {weekly_paragraphs}
        </td>
      </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f7fafc;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f7fafc;padding:30px 0;">
  <tr><td align="center">
    <table width="660" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

      <!-- HEADER -->
      <tr>
        <td style="background:#1a202c;padding:28px 36px;">
          <p style="margin:0;color:#a0aec0;font-size:11px;font-family:Arial,sans-serif;letter-spacing:2px;text-transform:uppercase;">{SUBJECT_PREFIX}</p>
          <h1 style="margin:6px 0 4px 0;color:#ffffff;font-size:22px;font-family:Arial,sans-serif;font-weight:600;">{today}</h1>
          <p style="margin:0;color:#718096;font-size:13px;font-family:Arial,sans-serif;">{story_count} stories · Past 24 hours</p>
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

      <!-- STORIES -->
      <tr>
        <td style="padding:32px 36px 0 36px;">
          {story_cards}
        </td>
      </tr>

      <!-- SYNTHESIS -->
      <tr>
        <td style="padding:32px 36px;background:#fffbeb;border-top:2px solid #f6e05e;">
          <p style="margin:0 0 4px 0;font-size:11px;font-family:Arial,sans-serif;color:#b7791f;letter-spacing:2px;text-transform:uppercase;">AI Synthesis · {story_count + 1} of {story_count + 1}</p>
          <h2 style="margin:0 0 20px 0;font-size:18px;font-family:Arial,sans-serif;color:#1a202c;">🧠 What This Means for Human Dignity & Labor</h2>
          {synthesis_paragraphs}
        </td>
      </tr>

      {weekly_section}

      <!-- FOOTER -->
      <tr>
        <td style="padding:20px 36px;background:#f7fafc;border-top:1px solid #e2e8f0;">
          <p style="margin:0;font-size:11px;color:#a0aec0;font-family:Arial,sans-serif;text-align:center;">
            {SUBJECT_PREFIX} · Powered by Claude AI · Auto-delivered at 7AM EST
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
    print(f"\n🌅 Daily News Digest v4 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    if not NEWS_API_KEY:
        print("❌ NEWS_API_KEY not set. Add it to GitHub Secrets.")
        return

    stories = fetch_top_story_per_topic()
    if not stories:
        print("⚠️  No stories found. Check NEWS_API_KEY or topic keywords.")
        return

    analyzed_stories, synthesis = analyze_all_stories(stories)
    save_memory(analyzed_stories, synthesis)

    weekly = ""
    if datetime.now().weekday() == 6:  # Sunday
        weekly = generate_weekly_reflection()

    today   = datetime.now().strftime("%A, %B %d")
    subject = f"{SUBJECT_PREFIX} — {today}"
    if weekly:
        subject += " · 📆 Weekly Review"

    html = build_html_email(analyzed_stories, synthesis, weekly)
    send_email(subject, html)
    print("\n✅ Done!")


if __name__ == "__main__":
    run_daily()
