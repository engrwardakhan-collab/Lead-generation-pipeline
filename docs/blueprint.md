# ConvertWithAI Pipeline — Full Build Blueprint

> **Visual workflow with build status:** see [docs/workflow.md](workflow.md)
> Update `workflow.md` whenever a stage is completed or the flow changes.

---

## 1. PROJECT OVERVIEW

ConvertWithAI is an AI automation brand serving real estate professionals.
Fully autonomous lead generation pipeline that scrapes, enriches, personalizes,
and sends cold emails to real estate decision makers (brokers, owners, team leaders,
and CEOs) at near zero cost. Solo agents are filtered out — only people who control
a team or brokerage budget are targeted.

**Core Stats**
- 70% reduction in admin time for real estate agents
- Agents waste 15+ hours weekly on manual tasks
- AI handles lead capture, follow-ups, and scheduling 24/7
- Target: 10 Founding Members at 50% lifetime discount
- Pipeline cost: ~$10 one-time + $3/month

---

## 2. TECH STACK

| Component       | Tool                        | Cost              |
|-----------------|-----------------------------|-------------------|
| Scraping        | Python + Playwright         | $0                |
| Database        | Supabase (free tier)        | $0                |
| Email Sending   | Brevo SMTP (300/day)        | $0                |
| AI Agents       | OpenAI GPT-4o-mini          | ~$3/month         |
| Webhook/API     | Flask (Python)              | $0                |
| Hosting/Cron    | Railway (free tier)         | $0                |
| Cold Email Domain | Porkbun domain            | $6.99 one-time    |
| Notifications   | Telegram Bot                | $0                |
| Landing/CRM     | systeme.io                  | Already owned     |
| TOTAL           |                             | ~$10 + $3/month   |

---

## 3. FULL PIPELINE ARCHITECTURE

### Data Flow — 5 Sequential Stages

**Stage 1 — Scraper Layer**
- Sources: Realtor.com, Zillow, Google Maps, Redfin
- Target: Decision makers only — brokers, owners, team leaders, CEOs (title keyword filter applied at scrape time)
- Extracts: Name, title/designation, brokerage, location, phone, website URL, listing count
- Tool: Python + Playwright with random delays and user-agent rotation
- Output: Raw leads stored in Supabase (status = "new")

**Stage 2 — Enrichment Layer**
- For each scraped lead that has a personal website URL, fetch the homepage
- Scan nav links for a contact/about page; fetch it if found
- GPT-4o-mini reads the page text and extracts the email address (temp=0)
- SMTP verification (MX lookup + RCPT TO on port 25) confirms the address is deliverable
- Only SMTP-verified emails proceed to personalization; all others discarded

**Stage 3 — Personalization Layer**
- GPT-4o-mini reads each agent profile from Supabase
- Writes ONE custom opening line per lead (max 15 words)
- Example: "Hey Sarah — saw your 12 listings in Miami Beach this quarter..."
- Personalized line injected into email template

**Stage 4 — Email Layer**
- Brevo SMTP sends 50 emails/day (scales to 300/day)
- Plain text only — higher deliverability, avoids spam filters
- 3-email sequence: Day 1 intro / Day 3 follow-up / Day 7 urgency
- Unique tracking link per lead

**Stage 5 — Reply + Notification Layer**
- IMAP monitors inbox every 30 minutes
- GPT-4o-mini classifies replies: Interested / Not Interested / Unsubscribe
- Interested reply → Telegram notification sent instantly
- Click on link → webhook fires to systeme.io → audit booked

---

## 4. PROJECT FOLDER STRUCTURE

```
convertwithai-pipeline/
├── agents/
│   ├── orchestrator.py       ← Master controller (runs daily at 6am)
│   ├── personalization.py    ← Writes custom first lines
│   ├── email_agent.py        ← Sends sequences via Brevo
│   └── reply_agent.py        ← Monitors inbox + classifies
│
├── scrapers/
│   ├── realtor_scraper.py    ← DONE
│   ├── zillow_scraper.py
│   ├── gmaps_scraper.py
│   └── redfin_scraper.py
│
├── enrichers/
│   ├── base.py               ← DONE
│   └── website_enricher.py   ← DONE
│
├── core/
│   ├── database.py           ← DONE
│   ├── models.py             ← DONE
│   ├── exceptions.py         ← DONE
│   ├── logging_config.py     ← DONE
│   ├── rate_limiter.py       ← DONE
│   ├── brevo_sender.py       ← TO BUILD
│   ├── smtp_verifier.py      ← DONE
│   └── telegram_bot.py       ← TO BUILD
│
├── config/
│   └── settings.py           ← DONE
│
├── webhook/
│   └── app.py                ← Flask → systeme.io
│
├── pipeline/
│   └── runner.py             ← DONE
│
├── docs/
│   └── blueprint.md          ← THIS FILE
│
├── CLAUDE.md
├── .env
├── .env.example
├── requirements.txt
└── audit_agent.py
```

---

## 5. SUPABASE DATABASE SCHEMA

**Table: leads**

| Column               | Type        | Description                                      |
|----------------------|-------------|--------------------------------------------------|
| id                   | uuid (PK)   | Auto-generated                                   |
| name                 | text        | Agent full name                                  |
| email                | text        | Verified email address                           |
| brokerage            | text        | Brokerage/agency name                            |
| phone                | text        | Phone number                                     |
| location             | text        | City, State                                      |
| website_url          | text        | Personal website URL                             |
| profile_url          | text        | Realtor.com/Zillow profile URL (upsert key)      |
| source               | text        | realtor/zillow/gmaps/redfin                      |
| listing_count        | integer     | Number of active listings                        |
| personalized_line    | text        | GPT-generated opening line (max 15 words)        |
| status               | text        | new/enriched/personalized/contacted/replied/interested/unsubscribed |
| email_sent_at        | timestamp   | When first email was sent                        |
| last_contacted       | timestamp   | Most recent contact                              |
| reply_classification | text        | interested/not_interested/unsubscribe            |
| scraped_at           | timestamp   | When lead was scraped                            |
| created_at           | timestamp   | Record creation time                             |

**Status Flow:**
```
new → enriched → personalized → contacted → replied → interested/unsubscribed
```

---

## 6. EMAIL SEQUENCES

### Sender Details
- Sender name: Warda Khan
- Sender email: warda.khan@convertwithai.tech
- SMTP: Brevo (smtp-relay.brevo.com:587)
- CTA link: convertwithai.systeme.io
- Format: Plain text only (better deliverability)
- Daily limit: Start 50/day → scale to 300/day

---

### Email 1 — Day 1 (Cold Intro)

**Subject:** Quick question [First Name]

```
Hey [First Name],

[Personalized line from GPT-4o-mini — max 15 words]

Quick question — how are you handling leads
that come in after hours or on weekends?

Most agents I talk to are losing deals
simply because follow-up is manual and slow.

We built an AI system specifically for real estate agents.
Cuts admin time by 70%. Responds to leads 24/7.

Worth a 15-minute chat?

Warda Khan
ConvertWithAI — convertwithai.systeme.io
```

---

### Email 2 — Day 3 (Follow Up)

**Subject:** Re: Quick question [First Name]

```
Hey [First Name],

Just bumping this up.

We have 10 founding member spots at 50% lifetime discount.
A few are already taken.

15 minutes could save you 15 hours a week.

Book here: convertwithai.systeme.io
```

---

### Email 3 — Day 7 (Final Urgency)

**Subject:** Last one [First Name]

```
Hey [First Name],

Won't bug you after this.

If manual follow-ups, missed leads, and admin
overhead aren't a problem — we're not a fit.

If they are — grab one of the last founding spots.

50% off. Lifetime.

convertwithai.systeme.io
```

---

## 7. DOMAIN + EMAIL SETUP

### Domain
- Provider: Porkbun.com — DONE ($6.99 paid)
- Always check domain history: web.archive.org before buying
- Never use main brand domain for cold email
- Cold email domain: convertwithai.tech

### DNS Records for Brevo

| Type | Host            | Value                                        |
|------|-----------------|----------------------------------------------|
| TXT  | @               | v=spf1 include:sendinblue.com ~all           |
| TXT  | mail._domainkey | [Brevo provides this — copy exactly]         |
| TXT  | _dmarc          | v=DMARC1; p=none; rua=mailto:you@gmail.com   |

### Domain Warmup Schedule

| Week    | Emails/Day  | Action                                                          |
|---------|-------------|-----------------------------------------------------------------|
| Week 1  | 10/day      | Send to friends/family — ask them to reply + mark Not Spam     |
| Week 2  | 25/day      | Continue warmup — check mail-tester.com score (target 8+/10)  |
| Week 3  | 50/day      | Go live with real leads                                         |
| Week 4+ | 100-300/day | Scale to full Brevo free tier capacity                          |

---

## 8. LEAD SOURCES

| Source          | Volume       | Email Available   | Scrape Difficulty | Priority     | Status    |
|-----------------|--------------|-------------------|-------------------|--------------|-----------|
| Realtor.com     | High         | Sometimes direct  | Easy              | Build First  | ✅ DONE   |
| Zillow          | High         | Via website       | Medium            | Build Second | TO BUILD  |
| Google Maps     | Medium       | Via website       | Easy              | Build Third  | TO BUILD  |
| Redfin          | Medium       | Via website       | Medium            | Build Fourth | TO BUILD  |
| ActiveRain.com  | Low-Medium   | Sometimes         | Easy              | Bonus        | LATER     |
| Local MLS dirs  | Medium       | Sometimes         | Easy              | Bonus        | LATER     |

### Daily Volume Projection (when all scrapers built)
Decision makers are ~15-25% of all agent listings. Projections adjusted accordingly:
- Realtor.com: ~40-50 decision makers/day
- Zillow: ~40-50 decision makers/day
- Google Maps: ~20-30 decision makers/day
- Redfin: ~20-25 decision makers/day
- Total scraped/day: ~120-155 decision makers
- Email verification hit rate: ~40-60%
- Target verified emails/day: ~50-90 (higher quality than 240-360 solo agents)

---

## 9. PERSONALIZATION RULES

- GPT model: GPT-4o-mini
- Max length: 15 words per opening line
- Tone: casual, specific, not salesy
- Must reference something real about the agent (city, listings, brokerage)
- Temperature: 0.7 (creative but controlled)
- Example output: "Hey Sarah — saw your 12 listings in Miami Beach this quarter"

---

## 10. REPLY CLASSIFICATION RULES

GPT-4o-mini classifies every reply into exactly one of:

| Classification   | Action                                              |
|------------------|-----------------------------------------------------|
| interested       | Send Telegram alert immediately                     |
| not_interested   | Update status in Supabase, stop sequence            |
| unsubscribe      | Remove from all future sends, update status         |

- Check inbox every 30 minutes via IMAP
- Temperature: 0 (deterministic classification)
- Response format: single word only (interested/not_interested/unsubscribe)

---

## 11. BUILD ORDER (Remaining)

```
1. agents/personalization.py     ← DONE ✅
2. core/brevo_sender.py          ← DONE ✅
3. agents/email_agent.py         ← DONE ✅
4. agents/reply_agent.py         ← DONE ✅
5. core/telegram_bot.py          ← DONE ✅
6. webhook/app.py                ← DONE ✅
7. agents/orchestrator.py       ← DONE ✅
8. scrapers/zillow_scraper.py   ← NEXT (remaining scrapers)
8. scrapers/zillow_scraper.py
9. scrapers/gmaps_scraper.py
10. scrapers/redfin_scraper.py
11. Deploy to Railway
```

---

## 12. SYSTEME.IO WEBHOOK

- Trigger: lead clicks tracking link in email
- Action: Flask webhook receives click event
- Result: systeme.io books audit call automatically
- URL: configured in SYSTEME_IO_WEBHOOK_URL env var