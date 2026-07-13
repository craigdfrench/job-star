// server/storage.ts
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { db, type IntakeRow, type AssetRow } from './db.js';

const INTAKES_ROOT = path.resolve(process.cwd(), 'intakes');

export type AssetKind = 'screenshot' | 'image' | 'upload' | 'audio' | 'screencapture';

export interface CreateIntakeInput {
  title: string;
  description?: string;
  tags?: string[];
  source?: string;
  transcript?: string | null;
}

export interface IntakeRecord {
  id: string;
  title: string;
  description: string;
  status: string;
  tags: string[];
  createdAt: string;
  updatedAt: string;
  source: string;
  transcript: string | null;
}

export interface AssetRecord {
  id: string;
  intakeId: string;
  kind: AssetKind;
  filename: string;
  storedPath: string;
  mimeType: string;
  sizeBytes: number;
  meta: Record<string, unknown>;
  createdAt: string;
}

export interface IntakeWithAssets extends IntakeRecord {
  assets: AssetRecord[];
}

function rowToIntake(row: IntakeRow): IntakeRecord {
  return {
    id: row.id,
    title: row.title,
    description: row.description,
    status: row.status,
    tags: safeParse(row.tags, []),
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    source: row.source,
    transcript: row.transcript,
  };
}

function rowToAsset(row: AssetRow): AssetRecord {
  return {
    id: row.id,
    intakeId: row.intake_id,
    kind: row.kind as AssetKind,
    filename: row.filename,
    storedPath: row.stored_path,
    mimeType: row.mime_type,
    sizeBytes: row.size_bytes,
    meta: safeParse(row.meta, {}),
    createdAt: row.created_at,
  };
}

function safeParse<T>(s: string, fallback: T): T {
  try {
    return JSON.parse(s) as T;
  } catch {
    return fallback;
  }
}

function nowIso(): string {
  return new Date().toISOString();
}

function newId(prefix: string): string {
  return `${prefix}_${crypto.randomBytes(8).toString('hex')}`;
}

/** Directory on disk where an intake's files live. */
export function intakeDir(intakeId: string): string {
  return path.join(INTAKES_ROOT, intakeId);
}

/** Absolute path for an asset given its stored (relative) path. */
export function assetAbsPath(storedPath: string): string {
  return path.resolve(INTAKES_ROOT, storedPath);
}

const insertIntakeStmt = db.prepare<
  IntakeRow
>(/* sql */ `
  INSERT INTO intakes (id, title, description, status, tags, created_at, updated_at, source, transcript)
  VALUES (@id, @title, @description, @status, @tags, @created_at, @updated_at, @source, @transcript)
`);

const insertAssetStmt = db.prepare<
  AssetRow
>(/* sql */ `
  INSERT INTO assets (id, intake_id, kind, filename, stored_path, mime_type, size_bytes, meta, created_at)
  VALUES (@id, @intake_id, @kind, @filename, @stored_path, @mime_type, @size_bytes, @meta, @created_at)
`);

const getIntakeStmt = db.prepare<string, IntakeRow>(
  `SELECT * FROM intakes WHERE id = ?`
);

const getAssetsForIntakeStmt = db.prepare<string, AssetRow>(
  `SELECT * FROM assets WHERE intake_id = ? ORDER BY created_at ASC`
);

const listIntakesStmt = db.prepare<
  { limit: number; offset: number },
  IntakeRow
>(/* sql */ `
  SELECT * FROM intakes ORDER BY created_at DESC LIMIT @limit OFFSET @offset
`);

const countIntakesStmt = db.prepare<[], { c: number }>(
  `SELECT COUNT(*) AS c FROM intakes`
);

const deleteIntakeStmt = db.prepare<string>(
  `DELETE FROM intakes WHERE id = ?`
);

const updateIntakeStmt = db.prepare<
  { id: string; title?: string; description?: string; status?: string; tags?: string; updated_at: string },
  IntakeRow
>(/* sql */ `
  UPDATE intakes
  SET title       = COALESCE(@title, title),
      description = COALESCE(@description, description),
      status      = COALESCE(@status, status),
      tags        = COALESCE(@tags, tags),
      updated_at  = @updated_at
  WHERE id = @id
`);

export function createIntake(input: CreateIntakeInput): IntakeRecord {
  const id = newId('intake');
  const ts = nowIso();
  const row: IntakeRow = {
    id,
    title: input.title,
    description: input.description ?? '',
    status: 'new',
    tags: JSON.stringify(input.tags ?? []),
    created_at: ts,
    updated_at: ts,
    source: input.source ?? 'web',
    transcript: input.transcript ?? null,
  };

  // Create the on-disk directory for this intake's files.
  fs.mkdirSync(intakeDir(id), { recursive: true });

  insertIntakeStmt.run(row);
  return rowToIntake(row);
}

export function getIntake(id: string): IntakeRecord | null {
  const row = getIntakeStmt.get(id);
  return row ? rowToIntake(row) : null;
}

export function listIntakes(opts: { limit?: number; offset?: number } = {}): {
  items: IntakeRecord[];
  total: number;
} {
  const limit = Math.max(1, Math.min(200, opts.limit ?? 50));
  const offset = Math.max(0, opts.offset ?? 0);
  const rows = listIntakesStmt.all({ limit, offset });
  const total = countIntakesStmt.get()?.c ?? 0;
  return { items: rows.map(rowToIntake), total };
}

export interface AddAssetInput {
  intakeId: string;
  kind: AssetKind;
  filename: string;
  mimeType: string;
  sizeBytes: number;
  /** File extension to use when materializing the file (e.g. "png", "webm"). */
  ext: string;
  meta?: Record<string, unknown>;
  /** Buffer / stream contents to write to disk. */
  data: Buffer;
}

export function addAsset(input: AddAssetInput): AssetRecord {
  const intake = getIntakeStmt.get(input.intakeId);
  if (!intake) {
    throw new Error(`Intake not found: ${input.intakeId}`);
  }

  const id = newId('asset');
  const safeExt = input.ext.replace(/[^a-z0-9]/gi, '').toLowerCase() || 'bin';
  const storedName = `${id}.${safeExt}`;
  // stored_path is relative to INTAKES_ROOT so it stays portable.
  const storedPath = path.join(input.intakeId, storedName);
  const absPath = assetAbsPath(storedPath);

  fs.mkdirSync(path.dirname(absPath), { recursive: true });
  fs.writeFileSync(absPath, input.data);

  const row: AssetRow = {
    id,
    intake_id: input.intakeId,
    kind: input.kind,
    filename: input.filename,
    stored_path: storedPath,
    mime_type: input.mimeType,
    size_bytes: input.sizeBytes,
    meta: JSON.stringify(input.meta ?? {}),
    created_at: nowIso(),
  };

  insertAssetStmt.run(row);
  return rowToAsset(row);
}

export function getAssetsForIntake(intakeId: string): AssetRecord[] {
  return getAssetsForIntakeStmt.all(intakeId).map(rowToAsset);
}

export function getIntakeWithAssets(id: string): IntakeWithAssets | null {
  const intake = getIntake(id);
  if (!intake) return null;
  return { ...intake, assets: getAssetsForIntake(id) };
}

export function updateIntake(
  id: string,
  patch: {
    title?: string;
    description?: string;
    status?: string;
    tags?: string[];
  }
): IntakeRecord | null {
  const existing = getIntakeStmt.get(id);
  if (!existing) return null;

  updateIntakeStmt.run({
    id,
    title: patch.title,
    description: patch.description,
    status: patch.status,
    tags: patch.tags ? JSON.stringify(patch.tags) : undefined,
    updated_at: nowIso(),
  });

  const updated = getIntakeStmt.get(id);
  return updated ? rowToIntake(updated) : null;
}

export function deleteIntake(id: string): boolean {
  const intake = getIntakeStmt.get(id);
  if (!intake) return false;

  // Cascade delete removes assets rows; clean up files on disk.
  const dir = intakeDir(id);
  deleteIntakeStmt.run(id);
  fs.rmSync(dir, { recursive: true, force: true });
  return true;
}

export { INTAKES_ROOT };
