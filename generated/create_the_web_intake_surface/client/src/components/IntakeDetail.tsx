// client/src/components/IntakeDetail.tsx
import { useEffect, useState } from 'react';

interface Asset {
  id: string;
  kind: 'image' | 'audio' | 'file' | 'screenshot' | 'screenCapture';
  filename: string;
  mimeType: string;
  size: number;
  url: string;
}

interface IntakeFull {
  id: string;
  createdAt: string;
  goal: string;
  notes?: string;
  textContext?: string;
  assets: Asset[];
}

interface IntakeDetailProps {
  intakeId: string;
  onBack: () => void;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function IntakeDetail({ intakeId, onBack }: IntakeDetailProps) {
  const [intake, setIntake] = useState<IntakeFull | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIntake(null);
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const res = await fetch(`/api/intakes/${intakeId}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) setIntake(data.intake);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [intakeId]);

  if (loading) return <div className="intake-detail loading">Loading…</div>;
  if (error) return <div className="intake-detail error">Error: {error}</div>;
  if (!intake) return <div className="intake-detail empty">Not found.</div>;

  const images = intake.assets.filter((a) => a.kind === 'image' || a.kind === 'screenshot' || a.kind === 'screenCapture');
  const audios = intake.assets.filter((a) => a.kind === 'audio');
  const files = intake.assets.filter((a) => a.kind === 'file');

  return (
    <div className="intake-detail">
      <div className="intake-detail-header">
        <button className="back-btn" onClick={onBack}>← Back to list</button>
        <h2>{intake.goal || '(no goal summary)'}</h2>
        <div className="intake-detail-time">{new Date(intake.createdAt).toLocaleString()}</div>
      </div>

      <section className="detail-section">
        <h3>Goal</h3>
        <p>{intake.goal || '—'}</p>
      </section>

      {intake.textContext && (
        <section className="detail-section">
          <h3>Text Context</h3>
          <pre className="text-context">{intake.textContext}</pre>
        </section>
      )}

      {intake.notes && (
        <section className="detail-section">
          <h3>Notes</h3>
          <p>{intake.notes}</p>
        </section>
      )}

      {images.length > 0 && (
        <section className="detail-section">
          <h3>Images ({images.length})</h3>
          <div className="image-grid">
            {images.map((img) => (
              <figure key={img.id} className="image-figure">
                <img src={img.url} alt={img.filename} />
                <figcaption>
                  {img.kind} · {formatSize(img.size)}
                </figcaption>
              </figure>
            ))}
          </div>
        </section>
      )}

      {audios.length > 0 && (
        <section className="detail-section">
          <h3>Audio ({audios.length})</h3>
          <ul className="audio-list">
            {audios.map((a) => (
              <li key={a.id} className="audio-item">
                <div className="audio-meta">
                  {a.filename} · {formatSize(a.size)}
                </div>
                <audio controls src={a.url} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {files.length > 0 && (
        <section className="detail-section">
          <h3>Files ({files.length})</h3>
          <ul className="file-list">
            {files.map((f) => (
              <li key={f.id} className="file-item">
                <a href={f.url} target="_blank" rel="noreferrer" download={f.filename}>
                  {f.filename}
                </a>
                <span className="file-size">{formatSize(f.size)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
