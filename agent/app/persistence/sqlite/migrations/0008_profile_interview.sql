CREATE TABLE IF NOT EXISTS profile_interview_sessions (
  id TEXT PRIMARY KEY,
  source_profile_path TEXT NOT NULL,
  target_profile_path TEXT NOT NULL,
  status TEXT NOT NULL,
  current_item_id TEXT,
  current_question TEXT,
  current_gap TEXT,
  state_json TEXT NOT NULL,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS profile_interview_turns (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  question_id TEXT,
  question_text TEXT,
  user_answer TEXT NOT NULL,
  interpreted_answer_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES profile_interview_sessions(id)
);

CREATE TABLE IF NOT EXISTS profile_interview_item_drafts (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  status TEXT NOT NULL,
  completeness_score REAL NOT NULL DEFAULT 0,
  item_json TEXT NOT NULL,
  gap_summary_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES profile_interview_sessions(id)
);
