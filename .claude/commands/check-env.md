# Check Environment

Verify that all required .env variables are set for the ConvertWithAI pipeline.

Read the .env file and check every variable against this required list:

**Required (pipeline won't start without these):**
- SUPABASE_URL
- SUPABASE_KEY
- OPENAI_API_KEY
- BREVO_SMTP_LOGIN
- BREVO_SMTP_KEY
- BREVO_SENDER_EMAIL
- IMAP_SERVER
- IMAP_USERNAME
- IMAP_PASSWORD

**Optional (features degrade gracefully without these):**
- SYSTEME_IO_WEBHOOK_URL
- WEBHOOK_BASE_URL

For each variable show:
- Set or Missing
- If set: show first 6 chars + **** (never show full value)
- If missing: warn clearly

Then give an overall verdict: READY TO RUN or BLOCKED (list what's missing).
