import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
  item_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  price REAL, currency TEXT,
  url TEXT, seller TEXT, buying_options TEXT,
  created_at TEXT,
  first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  last_seen_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listings_first_seen ON listings(first_seen_at);

CREATE TABLE IF NOT EXISTS listing_images (
  item_id TEXT NOT NULL REFERENCES listings(item_id),
  position INTEGER NOT NULL,
  url TEXT NOT NULL,
  local_path TEXT,                       -- filled in Phase 2
  PRIMARY KEY (item_id, position)
);

CREATE TABLE IF NOT EXISTS listing_matches (
  item_id TEXT NOT NULL REFERENCES listings(item_id),
  search_name TEXT NOT NULL,
  matched_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (item_id, search_name)
);

CREATE TABLE IF NOT EXISTS poll_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  search_name TEXT NOT NULL,
  started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  calls INTEGER NOT NULL DEFAULT 0,
  fetched INTEGER NOT NULL DEFAULT 0,
  new_listings INTEGER NOT NULL DEFAULT 0,
  error TEXT
);

CREATE TABLE IF NOT EXISTS api_usage (
  day TEXT PRIMARY KEY,                  -- UTC date
  calls INTEGER NOT NULL DEFAULT 0
);

-- What the card IS (set/year/number...). Filled from eBay item specifics when we fetched the listing's
-- details, with title parsing as a fallback; aspects_json keeps eBay's raw item specifics.
CREATE TABLE IF NOT EXISTS card_details (
  item_id TEXT PRIMARY KEY REFERENCES listings(item_id),
  card_name TEXT, set_name TEXT, year INTEGER, card_number TEXT,
  rarity TEXT, finish TEXT, language TEXT,
  source TEXT,                           -- ebay_aspects | title (+vision when Claude supplied the name)
  aspects_json TEXT,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Phase 2 (created now so migrations stay trivial)
CREATE TABLE IF NOT EXISTS assessments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL REFERENCES listings(item_id),
  model TEXT, grade TEXT, verdict TEXT, defects_json TEXT, confidence REAL, cost_usd REAL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS notifications (
  item_id TEXT PRIMARY KEY REFERENCES listings(item_id),
  channel TEXT, sent_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Convenience: everything that passed or needs a manual look, with card info.  SELECT * FROM matches;
CREATE VIEW IF NOT EXISTS matches AS
SELECT l.item_id, a.verdict, a.grade, l.price, l.currency, l.title,
       c.card_name, c.set_name, c.year, c.card_number, c.rarity, c.finish, c.language,
       l.url, l.created_at AS listed_at, a.created_at AS assessed_at
FROM assessments a
JOIN listings l USING (item_id)
LEFT JOIN card_details c USING (item_id)
WHERE a.verdict IN ('pass', 'review');
"""


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(assessments)")}
    if "verdict" not in cols:  # migrate DBs created by Phase 1
        conn.execute("ALTER TABLE assessments ADD COLUMN verdict TEXT")
    return conn
