// client/src/components/IntakeList.tsx
import { useEffect, useState } from 'react';

interface IntakeSummary {
  id: string;
  createdAt: string;
  goal: string;
  assetCount: number;
  assetBreakdown: Record<string, number>;
}

interface IntakeListProps {
  onSelect: (id: string) => void;
}

const KIND_LABELS: Record<string, string> = {
  image: 'img',
  audio: 'audio',
  file: 'file',
  screenshot: 'shot',
  screenCapture: 'screen',
};

export function IntakeList({ onSelect }: IntakeListProps) {
  const [intakes, setIntakes] = useState<IntakeSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch('/api/intakes');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) setIntakes(data.intakes ?? []);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return <div className="intake-list loading">Loading intakes…</div>;
  if (error) return <div className="intake-list error">Error: {error}</div>;
  if (intakes.length === 0)
    return <div className="intake-list empty">No intakes yet. Submit one to see it here.</div>;

  return (
    <div className="intake-list">
      <h2>Recent Intakes</h2>
      <ul className="intake-list-items">
        {intakes.map((it) => (
          <li key={it.id} className="intake-list-item">
            <button className="intake-row" onClick={() => onSelect(it.id)}>
              <div className="intake-row-main">
                <div className="intake-row-goal">{it.goal || '(no goal summary)'}</div>
                <div className="intake-row-time">{new Date(it.createdAt).toLocaleString()}</div>
              </div>
              <div className="intake-row-meta">
                <span className="asset-count">{it.assetCount} assets</span>
                <span className="asset-breakdown">
                  {Object.entries(it.assetBreakdown).map(([kind, n]) => (
                    <span key={kind} className="asset-pill">
                      {n} {KIND_LABELS[kind] ?? kind}
                    </span>
                  ))}
                </span>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
