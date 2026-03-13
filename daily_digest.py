"""
Daily News Digest v5
- Five stories per topic (past 24 hours, trusted sources only)
- Claude selects most relevant articles from fetched pool
- Each story: What Happened + What the Source Said + AI Interpretation + Plain English
- Final section: AI synthesis that directly references story content
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
SUBJECT_PREFIX      = CONFIG.get("email_subject_prefix", "Daily Digest")
STORIES_PER_TOPIC   = CONFIG.get("stories_per_topic", 5)
TRUSTED_DOMAINS     = ",".join(CONFIG.get("trusted_domains", []))

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
# STEP 1: FETCH ARTICLES FROM TRUSTED SOURCES
# ─────────────────────────────────────────────

def fetch_articles_for_topic(topic) -> list:
    """Fetch up to 20 candidate articles from trusted sources within 24h (72h fallback)."""
    topic_name = get_topic_name(topic)
    keywords   = get_topic_keywords(topic)

    # Use keywords as-is (already short single-concept terms in config)
    terms = [kw.strip() for kw in keywords[:8]]
    query = " OR ".join(terms)
    print(f"    Query: {query}")

    for hours_back in [24, 72]:
        try:
            params = {
                "q":        query,
                "language": "en",
                "sortBy":   "publishedAt",
                "pageSize": 20,
                "from":     (datetime.now() - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%S"),
            }
            if TRUSTED_DOMAINS:
                params["domains"] = TRUSTED_DOMAINS

            response = requests.get(
                "https://newsapi.org/v2/everything",
                params=params,
                headers={"X-Api-Key": NEWS_API_KEY},
                timeout=10,
            )
            data = response.json()

            if data.get("status") != "ok":
                print(f"  NewsAPI error for [{topic_name}]: {data.get('message')}")
                break

            articles = [
                a for a in data.get("articles", [])
                if a.get("title") and a.get("title") != "[Removed]"
                and a.get("description")
            ]

            if articles:
                label = f"{hours_back}h"
                print(f"  [{topic_name}] {len(articles)} candidates ({label})")
                return articles
            elif hours_back == 24:
                print(f"  [{topic_name}] Nothing in 24h, trying 72h...")

        except Exception as e:
            print(f"  Failed to fetch [{topic_name}]: {e}")
            break

    print(f"  [{topic_name}] No articles found.")
    return []


def select_top_stories(topic, articles: list, client) -> list:
    """Use Claude to select the STORIES_PER_TOPIC most relevant articles for this topic."""
    topic_name = get_topic_name(topic)
    topic_desc = get_topic_description(topic)

    # If we already have few enough articles, let Claude still validate relevance
    if not articles:
        return []

    articles_text = "\n\n".join(
        f"[{i}] Source: {a.get('source', {}).get('name', 'Unknown')}\n"
        f"Title: {a.get('title', '')}\n"
        f"Description: {(a.get('description') or '')[:280]}"
        for i, a in enumerate(articles)
    )

    prompt = f"""You are a news curator. Select the {STORIES_PER_TOPIC} best articles for this topic.

TOPIC: {topic_name}
DESCRIPTION: {topic_desc}

SELECTION RULES — all must be met for an article to qualify:
1. Directly and substantively relevant to the topic — not tangentially related
2. Has policy depth, expert analysis, institutional significance, or meaningful data
3. Not a product announcement, software release, personal blog, or local story without broader significance
4. The source and content clearly connect to the topic's core concern

ARTICLES:
{articles_text}

Reply ONLY with a JSON array of indices (numbers) for the {STORIES_PER_TOPIC} best articles.
Order them best-first. Example: [3, 0, 7, 2, 5]
If fewer than {STORIES_PER_TOPIC} qualify, return only the qualifying indices.
If none qualify, return [].
Reply with the JSON array ONLY — no explanation."""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()
        # Extract JSON array even if there's surrounding text
        match = re.search(r'\[[\d,\s]*\]', raw)
        if match:
            indices = json.loads(match.group())
            selected = [articles[i] for i in indices if isinstance(i, int) and i < len(articles)]
            if selected:
                return selected
    except Exception as e:
        print(f"    Selection error for [{topic_name}]: {e}")

    # Fallback: return first N articles
    return articles[:STORIES_PER_TOPIC]


def fetch_all_topics(client) -> list:
    """Fetch and select stories for all topics. Returns list of topic dicts."""
    print("📡 Fetching stories from trusted sources...")
    topics_data = []

    for topic in RAW_TOPICS:
        topic_name = get_topic_name(topic)
        articles   = fetch_articles_for_topic(topic)

        if not articles:
            print(f"  ⚠️  Skipping [{topic_name}] — no articles found")
            continue

        selected = select_top_stories(topic, articles, client)
        print(f"  ✓ [{topic_name}] Selected {len(selected)} stories")

        stories = [
            {
                "topic_name":        topic_name,
                "topic_description": get_topic_description(topic),
                "title":             a.get("title", ""),
                "description":       (a.get("description") or "")[:600],
                "content":           (a.get("content") or "")[:800],
                "source":            a.get("source", {}).get("name", "Unknown"),
                "author":            a.get("author", ""),
                "url":               a.get("url", ""),
                "published":         a.get("publishedAt", ""),
            }
            for a in selected
        ]

        topics_data.append({
            "topic_name":        topic_name,
            "topic_description": get_topic_description(topic),
            "stories":           stories,
        })

    total = sum(len(td["stories"]) for td in topics_data)
    print(f"\n  Total: {total} stories across {len(topics_data)} topics")
    return topics_data


# ─────────────────────────────────────────────
# STEP 2: ANALYZE STORIES WITH CLAUDE
# ─────────────────────────────────────────────

def analyze_story(story: dict, client) -> dict:
    """Ask Claude to analyze a single story in the 4-section structure."""

    prompt = f"""You are a news analyst writing for this audience:
"{AUDIENCE}"

Analyze this story and respond in exactly this JSON structure.
Plain English — short sentences, no jargon, no buzzwords.

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
  "source_comments": "2-3 sentences on how {story['source']} framed this story. What angle did they take? What did they emphasize or downplay?",
  "ai_interpretation": "2-3 sentences. What does this actually mean? Cut through the noise. What is the real significance for the topic: {story['topic_name']}?",
  "plain_explanation": "2-3 sentences. Explain this to a smart person who knows nothing about this topic. What is the bigger picture and why does it matter?"
}}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
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


def generate_synthesis(topics_data: list, client) -> str:
    """Generate AI synthesis that directly references the analyzed story content."""

    sections = []
    for td in topics_data:
        story_lines = []
        for i, s in enumerate(td["stories"], 1):
            # Use the analyzed interpretation if available, else raw description
            significance = s.get("analysis", {}).get("ai_interpretation") or s["description"][:200]
            story_lines.append(
                f"  [{i}] \"{s['title']}\" — {s['source']}\n"
                f"       Significance: {significance}"
            )
        sections.append(f"TOPIC: {td['topic_name']}\n" + "\n".join(story_lines))

    all_content = "\n\n".join(sections)

    prompt = f"""You are a senior policy analyst. Below are today's top stories across five AI-related topics,
with their key significance already extracted.

{all_content}

Write a synthesis section titled "What This Means for Human Dignity & Labor".

REQUIREMENTS:
- Directly cite specific stories by referencing the actor, institution, or country by name
  (e.g., "The EU's new framework reported by Reuters...", "As the NYT noted regarding X's decision...")
- Draw concrete connections ACROSS topics — show how governance, religion, education, labor, and government
  policy developments interact or reinforce each other today
- Address the central question directly and specifically:
  What do TODAY's stories collectively tell us about the shift from performance-based identity
  toward intrinsic human worth as AI displaces human contribution?
- State clearly whether today's news ACCELERATES, RESISTS, or COMPLICATES this shift — with evidence
- End with ONE concrete, actionable observation the reader can act on or watch for
- Plain English, short sentences, 4–5 paragraphs maximum
- Every claim must connect to a specific story above — no generic statements

Do NOT write vague generalities like "AI is changing society." Be specific. Quote actors and findings."""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text.strip()


def analyze_all_topics(topics_data: list, client) -> tuple:
    """Run Claude analysis on every story, then generate synthesis."""
    print("🤖 Analyzing stories with Claude...")

    for td in topics_data:
        topic_name = td["topic_name"]
        for i, story in enumerate(td["stories"], 1):
            print(f"  → [{topic_name}] Story {i}/{len(td['stories'])}...")
            story["analysis"] = analyze_story(story, client)

    print("  → Generating synthesis...")
    synthesis = generate_synthesis(topics_data, client)

    return topics_data, synthesis


# ─────────────────────────────────────────────
# STEP 3: SAVE MEMORY
# ─────────────────────────────────────────────

def save_memory(topics_data: list, synthesis: str):
    today    = datetime.now().strftime("%Y-%m-%d")
    filepath = MEMORY_DIR / f"{today}.json"
    data = {
        "date":     today,
        "topics": [
            {
                "topic": td["topic_name"],
                "stories": [
                    {"title": s["title"], "source": s["source"], "url": s["url"]}
                    for s in td["stories"]
                ],
            }
            for td in topics_data
        ],
        "synthesis": synthesis,
    }
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  💾 Memory saved → {filepath}")


# ─────────────────────────────────────────────
# STEP 4: WEEKLY REFLECTION (Sundays)
# ─────────────────────────────────────────────

def generate_weekly_reflection(client) -> str:
    print("📆 Generating weekly reflection...")

    week_data = []
    for i in range(7):
        date     = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        filepath = MEMORY_DIR / f"{date}.json"
        if filepath.exists():
            with open(filepath) as f:
                week_data.append(json.load(f))

    if not week_data:
        return ""

    lines = []
    for d in week_data:
        day_lines = [d["date"] + ":"]
        # Support both new format (topics[].stories[]) and old format (stories[])
        for entry in d.get("topics", []):
            topic = entry.get("topic", "")
            titles = "; ".join(s["title"] for s in entry.get("stories", []))
            day_lines.append(f"  [{topic}] {titles}")
        if not d.get("topics"):
            for s in d.get("stories", []):
                day_lines.append(f"  [{s.get('topic','')}] {s.get('title','')}")
        lines.append("\n".join(day_lines))

    week_text = "\n\n".join(lines)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": f"""Review this week's top stories and write a short weekly reflection.
Plain English. 3–4 paragraphs. Focus on the week's arc — what changed, what stayed the same,
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


def build_story_card(story: dict, story_num: int) -> str:
    a           = story.get("analysis", {})
    pub         = format_published(story["published"])
    author_line = f" · {story['author']}" if story.get("author") else ""

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:26px;">
      <tr><td style="padding-bottom:8px;">
        <h3 style="margin:0 0 4px 0;font-size:16px;font-family:Arial,sans-serif;color:#1a202c;line-height:1.4;">
          <span style="color:#a0aec0;font-size:13px;font-weight:400;">#{story_num}&nbsp;&nbsp;</span>
          <a href="{story['url']}" style="color:#1a202c;text-decoration:none;">{story['title']}</a>
        </h3>
        <p style="margin:0 0 12px 0;font-size:11px;font-family:Arial,sans-serif;color:#a0aec0;">
          {story['source']}{author_line} &nbsp;·&nbsp; {pub}
          &nbsp;·&nbsp; <a href="{story['url']}" style="color:#2b6cb0;">Read original →</a>
        </p>
      </td></tr>

      <tr><td style="padding-bottom:8px;">
        <p style="margin:0 0 3px 0;font-size:11px;font-family:Arial,sans-serif;color:#718096;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">🔍 What Happened</p>
        <p style="margin:0;font-size:14px;font-family:Georgia,serif;color:#2d3748;line-height:1.75;">{a.get('who_did_what','')}</p>
      </td></tr>

      <tr><td style="padding-bottom:8px;">
        <p style="margin:0 0 3px 0;font-size:11px;font-family:Arial,sans-serif;color:#718096;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">📰 What the Source Said</p>
        <p style="margin:0;font-size:14px;font-family:Georgia,serif;color:#2d3748;line-height:1.75;">{a.get('source_comments','')}</p>
      </td></tr>

      <tr><td style="padding-bottom:8px;">
        <p style="margin:0 0 3px 0;font-size:11px;font-family:Arial,sans-serif;color:#718096;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">🤖 AI Interpretation</p>
        <p style="margin:0;font-size:14px;font-family:Georgia,serif;color:#2d3748;line-height:1.75;">{a.get('ai_interpretation','')}</p>
      </td></tr>

      <tr><td>
        <p style="margin:0 0 3px 0;font-size:11px;font-family:Arial,sans-serif;color:#718096;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">💡 Plain English</p>
        <p style="margin:0;font-size:14px;font-family:Georgia,serif;color:#2d3748;line-height:1.75;">{a.get('plain_explanation','')}</p>
      </td></tr>
    </table>
    <hr style="border:none;border-top:1px solid #edf2f7;margin:0 0 22px 0;">
"""


def build_topic_section(td: dict, section_num: int) -> str:
    topic_name = td["topic_name"]
    story_cards = "".join(
        build_story_card(s, i) for i, s in enumerate(td["stories"], 1)
    )

    return f"""
      <tr>
        <td style="padding:30px 36px 4px 36px;border-top:3px solid #2b6cb0;">
          <p style="margin:0 0 2px 0;font-size:11px;font-family:Arial,sans-serif;color:#a0aec0;letter-spacing:2px;text-transform:uppercase;">Topic {section_num} of {len(RAW_TOPICS)}</p>
          <h2 style="margin:0 0 22px 0;font-size:19px;font-family:Arial,sans-serif;color:#1a202c;font-weight:700;">
            {topic_name}
            <span style="font-size:12px;font-weight:400;color:#718096;margin-left:8px;">{len(td['stories'])} stories</span>
          </h2>
          {story_cards}
        </td>
      </tr>
"""


def build_html_email(topics_data: list, synthesis: str, weekly: str = "") -> str:
    today         = datetime.now().strftime("%A, %B %d, %Y")
    total_stories = sum(len(td["stories"]) for td in topics_data)
    topic_names   = " &nbsp;·&nbsp; ".join(td["topic_name"] for td in topics_data)

    topic_sections = "".join(
        build_topic_section(td, i) for i, td in enumerate(topics_data, 1)
    )

    synthesis_paragraphs = "".join(
        f'<p style="margin:0 0 14px 0;font-size:15px;font-family:Georgia,serif;color:#2d3748;line-height:1.75;">{p.strip()}</p>'
        for p in synthesis.split("\n\n") if p.strip()
    )

    weekly_section = ""
    if weekly:
        weekly_paragraphs = "".join(
            f'<p style="margin:0 0 14px 0;font-size:15px;font-family:Georgia,serif;color:#2d3748;line-height:1.75;">{p.strip()}</p>'
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
    <table width="700" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

      <!-- HEADER -->
      <tr>
        <td style="background:#1a202c;padding:28px 36px;">
          <p style="margin:0;color:#a0aec0;font-size:11px;font-family:Arial,sans-serif;letter-spacing:2px;text-transform:uppercase;">{SUBJECT_PREFIX}</p>
          <h1 style="margin:6px 0 4px 0;color:#ffffff;font-size:22px;font-family:Arial,sans-serif;font-weight:600;">{today}</h1>
          <p style="margin:0;color:#718096;font-size:13px;font-family:Arial,sans-serif;">
            {total_stories} stories &nbsp;·&nbsp; {len(topics_data)} topics &nbsp;·&nbsp; Past 24–48 hours &nbsp;·&nbsp; Trusted sources only
          </p>
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

      <!-- TOPIC SECTIONS -->
      {topic_sections}

      <!-- SYNTHESIS -->
      <tr>
        <td style="padding:32px 36px;background:#fffbeb;border-top:3px solid #f6e05e;">
          <p style="margin:0 0 4px 0;font-size:11px;font-family:Arial,sans-serif;color:#b7791f;letter-spacing:2px;text-transform:uppercase;">AI Synthesis</p>
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
    msg            = MIMEMultipart("alternative")
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
    print(f"\n🌅 Daily News Digest v5 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    if not NEWS_API_KEY:
        print("❌ NEWS_API_KEY not set. Add it to GitHub Secrets.")
        return

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    topics_data = fetch_all_topics(client)
    if not topics_data:
        print("⚠️  No stories found. Check NEWS_API_KEY or topic keywords.")
        return

    topics_data, synthesis = analyze_all_topics(topics_data, client)
    save_memory(topics_data, synthesis)

    weekly = ""
    if datetime.now().weekday() == 6:  # Sunday
        weekly = generate_weekly_reflection(client)

    today   = datetime.now().strftime("%A, %B %d")
    subject = f"{SUBJECT_PREFIX} — {today}"
    if weekly:
        subject += " · Weekly Review"

    html = build_html_email(topics_data, synthesis, weekly)
    send_email(subject, html)
    print("\n✅ Done!")


if __name__ == "__main__":
    run_daily()
