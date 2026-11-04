import { useEffect, useRef, useState } from 'react';

/** "3 min ago" style relative time for an ISO string. */
export function ago(iso: string | null | undefined): string {
  if (!iso) return 'never';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 45) return 'just now';
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

/** mm:ss or h:mm:ss until an ISO time; '' when it is in the past or missing. */
export function countdown(iso: string | null | undefined, now: number): string {
  if (!iso) return '';
  const s = Math.floor((new Date(iso).getTime() - now) / 1000);
  if (s <= 0) return 'any moment';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}:${String(r).padStart(2, '0')}`;
}

/** Re-render every `ms` (used for countdowns). Returns Date.now(). */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(t); }, [ms]);
  return now;
}

/** Run `fn` now and then every `ms` while mounted. `fn` always sees the latest closure. */
export function usePoll(fn: () => void, ms: number, deps: unknown[] = []) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    ref.current();
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, ...deps]);
}

export const num = (n: number | undefined | null) => (n ?? 0).toLocaleString();
