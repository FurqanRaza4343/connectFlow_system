import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { ago, usePoll } from './util';

type N = { id: string; type: string; title: string; body: string; link: string; leadId: string | null; read: boolean; createdAt: string };
type Res = { rows: N[]; unread: number };

const ICON: Record<string, string> = { batch: '📦', reply: '💬', needs_human: '🙋', failed: '⚠️', session: '📵', info: 'ℹ️' };

/** Where a notification should take the user (hash without '#'). */
export function notificationTarget(n: Pick<N, 'type' | 'link'>): string {
  const l = (n.link || '').replace(/^#/, '');
  if (l) return l.startsWith('approvals') || l.startsWith('inbox') || l.startsWith('leads') || l.startsWith('setup') ? `outreach/${l}` : l;
  if (n.type === 'reply' || n.type === 'needs_human') return 'outreach/inbox';
  if (n.type === 'batch' || n.type === 'failed') return 'outreach/approvals';
  if (n.type === 'session') return 'numbers';
  return 'overview';
}

export function Bell({ onGo }: { onGo: (hash: string) => void }) {
  const [res, setRes] = useState<Res>({ rows: [], unread: 0 });
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  const load = useCallback(() => { api<Res>('/api/notifications').then(setRes).catch(() => { /* bell is optional: stay quiet */ }); }, []);
  usePoll(load, 30000);

  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    addEventListener('mousedown', down); addEventListener('keydown', key);
    return () => { removeEventListener('mousedown', down); removeEventListener('keydown', key); };
  }, [open]);

  const read = async (ids?: string[]) => { try { await api('/api/notifications/read', 'POST', ids ? { ids } : {}); load(); } catch { /* ignore */ } };
  const click = (n: N) => { if (!n.read) read([n.id]); setOpen(false); onGo(notificationTarget(n)); };

  return (
    <div className="bell" ref={box}>
      <button className="bell-btn" aria-label={`Notifications${res.unread ? `, ${res.unread} unread` : ''}`} aria-expanded={open} onClick={() => { setOpen(!open); if (!open) load(); }}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8a6 6 0 10-12 0c0 7-3 8-3 8h18s-3-1-3-8M13.7 20a2 2 0 01-3.4 0" /></svg>
        {res.unread > 0 && <span className="bell-badge">{res.unread > 99 ? '99+' : res.unread}</span>}
      </button>
      {open && (
        <div className="bell-pop" role="dialog" aria-label="Notifications">
          <div className="row between bell-head"><b>Notifications</b>{res.unread > 0 && <button className="link" onClick={() => read()}>Mark all read</button>}</div>
          <div className="bell-list">
            {res.rows.map((n) => (
              <button key={n.id} className={`bell-item${n.read ? '' : ' unread'}`} onClick={() => click(n)}>
                <span className="bell-ic" aria-hidden="true">{ICON[n.type] || '•'}</span>
                <span className="bell-tx"><b>{n.title}</b>{n.body && <small>{n.body.slice(0, 110)}</small>}<small className="muted">{ago(n.createdAt)}</small></span>
              </button>
            ))}
            {!res.rows.length && <p className="muted" style={{ padding: 18, margin: 0, textAlign: 'center' }}>You’re all caught up.</p>}
          </div>
        </div>
      )}
    </div>
  );
}
