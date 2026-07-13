// server/db.ts
import Database from 'better-sqlite3';
import path from 'node:path';
import fs from 'node:fs';

const DB_PATH = process.env.JOBSTAR_DB_PATH ?? path.resolve(process.cwd(), 'intakes.db');

// Ensure parent dir exists
const dbDir = path.dirname(DB_PATH);
if (!fs.existsSync(dbDir)) {
  fs.mkdirSync(dbDir, { recursive: true });
}

export const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

const SCHEMA = /* sql */ `
CREATE TABLE IF NOT EXISTS intakes (
  id            TEXT PRIMARY KEY,
  title         TEXT NOT NULL,
  description   TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'new',
  tags          TEXT NOT NULL DEFAULT '[]',        -- JSON array of strings
  created_at    TEXT NOT NULL,                     -- ISO-8601
  updated_at    TEXT NOT NULL,                     -- ISO-8601
  source        TEXT NOT NULL DEFAULT 'web',       -- web | cli | api
  transcript    TEXT                               -- optional voice transcript
);

CREATE TABLE IF NOT EXISTS assets (
  id            TEXT PRIMARY KEY,
  intake_id     TEXT NOT NULL,
  kind          TEXT NOT NULL,                     -- screenshot | image | upload | audio | screencapture
  filename      TEXT NOT NULL,                     -- original / display name
  stored_path   TEXT NOT NULL,                     -- relative path under intakes/<id>/
  mime_type     TEXT NOT NULL DEFAULT 'application/octet-stream',
  size_bytes    INTEGER NOT NULL DEFAULT 0,
  meta          TEXT NOT NULL DEFAULT '{}',        -- JSON blob (duration, dimensions, etc.)
  created_at    TEXT NOT NULL,
  FOREIGN KEY (intake_id) REFERENCES intakes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_assets_intake_id ON assets(intake_id);
CREATE INDEX IF NOT EXISTS idx_intakes_status   ON intakes(status);
CREATE INDEX IF NOT EXISTS idx_intakes_created  ON intakes(created_at DESC);
`;

db.exec(SCHEMA);

export type IntakeRow = {
  id: string;
  title: string;
  description: string;
  status: string;
  tags: string;          // JSON
  created_at: string;
  updated_at: string;
  source: string;
  transcript: string | null;
};

export type AssetRow = {
  id: string;
  intake_id: string;
  kind: string;
  filename: string;
  stored_path: string;
  mime_type: string;
  size_bytes: number;
  meta: string;          // JSON
  created_at: string;
};

export default db;
