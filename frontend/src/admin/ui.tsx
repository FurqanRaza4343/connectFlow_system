import { ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';

export function ago(iso?: string | null): string {
  if (!iso) return 'never';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 45) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 45) return `${Math.floor(s / 86400)} d ago`;
  return new Date(iso).toLocaleDateString();
}
export const abs = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : '');
export const When = ({ iso }: { iso?: string | null }) => <span title={abs(iso)} className={iso ? '' : 'ad-faint'}>{ago(iso)}</span>;
export const num = (n: number | undefined | null) => (n ?? 0).toLocaleString();

const TONE: Record<string, string> = {
  active: 'ok', ready: 'ok', approved: 'ok', success: 'ok', ok: 'ok', contacted: 'ok', replied: 'ok',
  suspended: 'bad', failed: 'bad', rejected: 'bad', disconnected: 'bad', revoked: 'bad', error: 'bad', stop: 'bad',
  pending: 'warn', qr_ready: 'warn', initializing: 'warn', retrying: 'warn', created: 'warn', new: 'info', verify: 'warn', hold: 'warn',
};
export const Badge = ({ v, tone }: { v: string; tone?: string }) => <span className={`ad-badge ${tone ?? TONE[v] ?? ''}`}>{v.replace('_', ' ')}</span>;

export function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return <span className="ad-copy" title="Click to copy" onClick={async (e) => { e.stopPropagation(); try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1200); } catch { /* ignore */ } }}>{done ? 'copied ✓' : text}</span>;
}

export const Loading = () => <div className="ad-load">Loading…</div>;
export const Empty = ({ children = 'Nothing here yet.' }: { children?: ReactNode }) => <div className="ad-empty">{children}</div>;
export const ErrorBox = ({ msg }: { msg: string }) => (msg ? <div className="ad-err" role="alert">{msg}</div> : null);

export function useDebounced<T>(v: T, ms = 300): T {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

/** GET helper with loading/error state and manual reload. */
export function useGet<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const load = useCallback(async () => {
    if (!path) return;
    const my = ++seq.current;
    setLoading(true);
    try { const r = await api<T>(path); if (my === seq.current) { setData(r); setErr(''); } }
    catch (e: any) { if (my === seq.current) setErr(e.message); }
    finally { if (my === seq.current) setLoading(false); }
  }, [path]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load, ...deps]);
  return { data, err, loading, reload: load, setErr };
}

export function Pager({ page, pages, total, onPage }: { page: number; pages: number; total: number; onPage: (p: number) => void }) {
  return (
    <div className="ad-pager"><span>{num(total)} result(s)</span>
      <span className="ad-row"><button className="ad-btn sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>← Prev</button><span>Page {page} / {pages}</span><button className="ad-btn sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next →</button></span></div>
  );
}

export function Confirm({ title, children, confirmLabel = 'Confirm', danger, requireText, onCancel, onConfirm }:
  { title: string; children?: ReactNode; confirmLabel?: string; danger?: boolean; requireText?: string; onCancel: () => void; onConfirm: () => Promise<void> | void }) {
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const ok = !requireText || typed.trim().toLowerCase() === requireText.trim().toLowerCase();
  return (
    <div className="ad-modal" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="ad-box" role="dialog" aria-modal="true" aria-label={title}>
        <h3 style={{ color: 'var(--text)', textTransform: 'none', fontSize: 16 }}>{title}</h3>
        <div className="ad-muted">{children}</div>
        {requireText && <label className="ad-muted">Type <b style={{ color: 'var(--text)' }}>{requireText}</b> to confirm<input className="ad-in" style={{ width: '100%', marginTop: 5 }} value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus /></label>}
        <div className="ad-row" style={{ justifyContent: 'flex-end' }}>
          <button className="ad-btn" onClick={onCancel}>Cancel</button>
          <button className={`ad-btn ${danger ? 'bad' : 'pri'}`} disabled={!ok || busy} onClick={async () => { setBusy(true); try { await onConfirm(); } finally { setBusy(false); } }}>{busy ? 'Working…' : confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}

/** 30-day style bar chart in plain CSS. */
export function BarChart({ data, label }: { data: { day: string; count: number }[]; label: string }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  if (!data.length) return <Empty>No data</Empty>;
  return (
    <div role="img" aria-label={label}>
      <div className="ad-bars">{data.map((d) => <i key={d.day} title={`${d.day}: ${d.count}`} style={{ height: `${Math.max(2, (d.count / max) * 100)}%` }} />)}</div>
      <div className="ad-chart-axis"><span>{data[0].day.slice(5)}</span><span>peak {num(max)}</span><span>{data[data.length - 1].day.slice(5)}</span></div>
    </div>
  );
}

export function HBars({ rows }: { rows: { label: string; value: number }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.length) return <Empty>No data</Empty>;
  return <div>{rows.map((r) => <div className="ad-hbar" key={r.label}><span>{r.label}</span><span className="t"><i style={{ width: `${(r.value / max) * 100}%` }} /></span><span className="ad-muted" style={{ textAlign: 'right' }}>{num(r.value)}</span></div>)}</div>;
}

export const Kpi = ({ v, l, s }: { v: ReactNode; l: string; s?: string }) => <div className="ad-kpi"><div className="v">{v}</div><div className="l">{l}</div>{s && <div className="s">{s}</div>}</div>;
