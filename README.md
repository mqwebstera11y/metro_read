# 📰 Daily News Digest

An automated AI-powered news digest that runs every morning at 7AM EST,
summarizes top stories by topic, adds historical context and impact analysis,
and emails you a clean briefing. Every Sunday it reflects on the whole week.

---

## 🚀 Setup (15 minutes)

### 1. Clone this repo
```bash
git clone https://github.com/YOUR_USERNAME/daily-news-digest.git
cd daily-news-digest
```

### 2. Install dependencies (for local testing)
```bash
pip install -r requirements.txt
```

### 3. Set your configuration
Edit `daily_digest.py` and update:
- `TOPICS` — the subjects you want to track
- `CONSTRAINTS` — your preferred analysis style and impact areas
- `RSS_FEEDS` — news sources (defaults are Reuters, BBC, NYT, WSJ)

### 4. Get your API keys

**Anthropic (Claude AI):**
- Sign up at https://console.anthropic.com
- Create an API key

**Gmail SMTP:**
- Enable 2-factor auth on your Google account
- Go to: Google Account → Security → App Passwords
- Create an app password for "Mail"

### 5. Test locally
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export SENDER_EMAIL="you@gmail.com"
export SENDER_PASSWORD="your-app-password"
export RECIPIENT_EMAIL="you@gmail.com"

python daily_digest.py
```

### 6. Add GitHub Secrets
In your GitHub repo → Settings → Secrets → Actions, add:
- `ANTHROPIC_API_KEY`
- `SENDER_EMAIL`
- `SENDER_PASSWORD`
- `RECIPIENT_EMAIL`

### 7. Enable GitHub Actions
- Push code to GitHub
- Go to Actions tab → enable workflows
- Use "Run workflow" button to test it manually first!

---

## 📁 Project Structure

```
daily-news-digest/
├── daily_digest.py              # Main script
├── requirements.txt             # Python dependencies
├── README.md                    # This file
├── .github/
│   └── workflows/
│       └── daily.yml            # GitHub Actions schedule
└── memory/
    ├── 2026-03-10.json          # Auto-saved daily digests
    ├── 2026-03-11.json
    └── ...
```

---

## 💰 Estimated Costs

| Service | Cost |
|---|---|
| GitHub Actions | Free (2,000 min/month free) |
| Claude Haiku API | ~$0.01–0.05 per day |
| Gmail SMTP | Free |
| **Total** | **~$1–2/month** |

---

## 🔧 Customization Tips

**Change topics:** Edit the `TOPICS` list in `daily_digest.py`

**Change time:** Edit the cron in `.github/workflows/daily.yml`
- `'0 12 * * *'` = 7AM EST (winter)
- `'0 11 * * *'` = 7AM EDT (summer/daylight saving)

**Higher quality summaries:** Change model to `claude-sonnet-4-6` (costs ~10x more)

**Add more news sources:** Add RSS feed URLs to the `RSS_FEEDS` list

---

## 🧠 How Memory Works

Each day's digest is saved as a JSON file in `/memory/`. On Sundays, the script
reads the last 7 days and generates a weekly reflection. These files are
auto-committed to your repo by the GitHub Actions bot.
