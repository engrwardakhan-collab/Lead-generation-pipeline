-- Adds a normalized email column + UNIQUE constraint so contact dedup is
-- enforced atomically by Postgres (core/database.py ContactRepository.upsert
-- catches the resulting 23505 unique_violation and treats it as "duplicate,
-- skip" instead of a hard failure).
--
-- Run this by hand in the Supabase SQL editor. Steps 1-2 are safe to run
-- immediately and are idempotent. Step 3 is a read-only check — run it and
-- resolve any rows it returns (decide which duplicate to keep) BEFORE
-- running step 4, which will fail loudly if duplicates still exist rather
-- than silently drop your data.

-- 1. Add the column. Nullable: contacts without a verified email stay NULL,
--    and Postgres never considers two NULLs a conflict under a UNIQUE
--    constraint, so unverified contacts are unaffected.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS email_canonical text;

-- 2. Backfill existing rows. Mirrors core/email_normalize.py's rules:
--    lowercase + trim, strip a '+tag' from the local part for every
--    provider, and additionally strip dots from the local part for
--    gmail.com/googlemail.com (folded into gmail.com).
UPDATE contacts
SET email_canonical = (
    CASE
        WHEN split_part(lower(trim(email)), '@', 2) IN ('gmail.com', 'googlemail.com')
            THEN replace(split_part(split_part(lower(trim(email)), '@', 1), '+', 1), '.', '')
                 || '@gmail.com'
        ELSE split_part(split_part(lower(trim(email)), '@', 1), '+', 1)
             || '@' || split_part(lower(trim(email)), '@', 2)
    END
)
WHERE email IS NOT NULL AND email_canonical IS NULL;

-- 3. Check for pre-existing duplicates before adding the constraint.
--    If this returns any rows, manually decide which contact to keep for
--    each email_canonical group and delete/merge the rest first.
SELECT email_canonical, array_agg(id) AS duplicate_ids, count(*) AS row_count
FROM contacts
WHERE email_canonical IS NOT NULL
GROUP BY email_canonical
HAVING count(*) > 1;

-- 4. Add the UNIQUE constraint once step 3 returns zero rows.
ALTER TABLE contacts ADD CONSTRAINT contacts_email_canonical_key UNIQUE (email_canonical);
