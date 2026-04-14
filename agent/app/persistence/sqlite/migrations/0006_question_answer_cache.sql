-- Human-approved answers to employer screening questions.
-- When a user resolves a gate question, the (label, answer) pair is saved here.
-- On future applications, answer_field.py does a keyword overlap search before
-- calling the LLM — if a match is found the cached answer is used directly.

CREATE TABLE IF NOT EXISTS question_answer_cache (
    id              TEXT PRIMARY KEY,
    question_raw    TEXT NOT NULL,           -- original label text as seen on the form
    question_norm   TEXT NOT NULL,           -- lower-cased, punctuation-stripped, stopwords removed
    answer          TEXT NOT NULL,           -- human-approved answer text
    field_type      TEXT,                    -- 'radio', 'select', 'text', etc.
    source          TEXT DEFAULT 'human',    -- 'human' (always for now)
    created_at      TEXT NOT NULL,
    use_count       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_qac_norm ON question_answer_cache(question_norm);
