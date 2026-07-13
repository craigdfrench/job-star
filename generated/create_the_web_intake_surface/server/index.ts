import express from 'express';
import cors from 'cors';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import intakeRoute from './routes/intake.js';
import { intakeStore } from './storage.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PORT = Number(process.env.PORT ?? 4319);
const HOST = process.env.HOST ?? '127.0.0.1';

const app = express();

app.use(cors({ origin: true }));
app.use(express.json({ limit: '2mb' }));

// Basic health check.
app.get('/api/health', (_req, res) => {
  res.json({ ok: true, service: 'job-star-intake', version: '0.1.0' });
});

// Intake surface.
app.use(intakeRoute);

// Serve stored intake assets statically (local-only dev convenience).
app.use(
  '/intakes',
  express.static(path.resolve(process.cwd(), 'intakes'), {
    dotfiles: 'ignore',
    index: false,
    maxAge: 0,
  }),
);

// 404 fallback.
app.use((_req, res) => {
  res.status(404).json({ error: 'not_found' });
});

// Centralized error handler (must have 4 args).
app.use((err: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  // Multer file-size errors land here.
  if (err && typeof err === 'object' && 'code' in err) {
    const code = (err as { code: string }).code;
    if (code === 'LIMIT_FILE_SIZE') {
      return res.status(413).json({ error: 'file_too_large', message: 'A file exceeded the 25 MB limit.' });
    }
    if (code === 'LIMIT_FILE_COUNT') {
      return res.status(400).json({ error: 'too_many_files', message: 'Too many files in one submission.' });
    }
    if (code === 'LIMIT_UNEXPECTED_FILE') {
      return res.status(400).json({ error: 'unexpected_field', message: 'An unexpected file field was sent.' });
    }
  }
  const message = err instanceof Error ? err.message : 'internal error';
  console.error('[server] unhandled:', err);
  res.status(500).json({ error: 'internal_error', message });
});

async function main() {
  await intakeStore.ensureRoot();
  app.listen(PORT, HOST, () => {
    console.log(`[job-star] intake server listening on http://${HOST}:${PORT}`);
  });
}

main().catch((err) => {
  console.error('[job-star] fatal:', err);
  process.exit(1);
});


// --- DUPLICATE BLOCK ---

// server/index.ts (excerpt - additions only)
import intakesRouter from './routes/intakes';

// ...existing app setup...
app.use('/api/intakes', intakesRouter);


// --- DUPLICATE BLOCK ---

// server/paths.ts
import path from 'path';

export const DATA_DIR = path.resolve(process.cwd(), 'data');
export const INTAKES_DIR = path.join(DATA_DIR, 'intakes');
