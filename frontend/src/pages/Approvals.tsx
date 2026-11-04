import { useCallback, useState } from 'react';
import { api, Me, Via, viaText } from '../api';
import { ago, countdown, num, usePoll, useNow } from '../components/util';

export type Draft = {
  id: string; leadId: string; company: string; whatsapp: string; kind: 'first' | 'followup' | 'reply'; body: string; incoming: string;
  status: string; note: string; source: 'ai' | 'manual' | 'fallback'; needsHuman: boolean; createdAt: string; via?: Via;
};
type Res = { rows: Draft[]; total: number; page: number; pages: number; counts: Record<string, number> };
type Queue = { sending: boolean; remaining: number; done: number; nextAt: string | null; current: string | null };
type Status = { dryRun: boolean; requireApproval: boolean };

const FILTERS: Array<[string, string]> = [['open', 'To review'], ['pending', 'Pending'], ['approved', 'Approved'], ['failed', 'Failed'], ['sent', 'Sent'], ['discarded', 'Discarded']];
const KIND: Record<string, string> = { first: 'First message', followup: 'Follow-up', reply: 'Reply' };
const TONE: Record<string, string> = { pending: 'warn', approved: 'ok', sending: 'ok', sent: 'ok', failed: 'bad', discarded: 'bad' };

export function Approvals({ me, go }: { me: Me; go: (hash: string) => void }) {
  const [filter, setFilter] = useState('open');
  const [kind, setKind] = useState('');
  const [page, setPage] = useState(1);
  const [res, setRes] = useState<Res | null>(null);
  const [queue, setQueue] = useState<Queue | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const now = useNow(1000);
  const canEdit = ['owner', 'admin', 'operator'].includes(me.role);

  const load = useCallback(() => {
    api<Res>(`/api/drafts?status=${filter}&kind=${kind}&page=${page}`).then((r) => { setRes(r); setErr(''); }).catch((e) => setErr(e.message));
    api<Queue>('/api/drafts/queue').then(setQueue).catch(() => { /* queue bar is optional */ });
  }, [filter, kind, page]);
  usePoll(load, queue?.sending ? 4000 : 12000, [filter, kind, page, queue?.sending]);
  usePoll(() => { api<Status>('/api/outreach/status').then(setStatus).catch(() => { /* ignore */ }); }, 30000);

  const act = async (fn: () => Promise<unknown>, ok = '') => { setErr(''); setMsg(''); try { await fn(); setMsg(ok); load(); } catch (e: any) { setErr(e.message); } };
  const toggle = (id: string) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const reviewable = (res?.rows || []).filter((d) => d.status === 'pending' || d.status === 'failed');
  const dry = !!status?.dryRun;
  const counts = res?.counts || {};
  const open = (counts.pending || 0) + (counts.approved || 0) + (counts.sending || 0) + (counts.failed || 0);

  const approve = (ids: string[]) => act(async () => { const r = await api<{ approved: number }>('/api/drafts/approve', 'POST', { ids }); setSel(new Set()); setMsg(`${r.approved} message(s) approved. They go out one at a time with a human-like gap.`); }, '');

  return (
    <div>
      {dry && <div className="banner amber-b"><span>🧪 <b>Dry-run is ON.</b> You can read, edit and regenerate drafts, but approving is disabled until you turn dry-run off in Setup.</span><button onClick={() => go('outreach/setup')}>Open Setup</button></div>}
      {err && <div className="err">{err}</div>}{msg && <div className="okbox">{msg}</div>}

      {queue && (queue.sending || queue.remaining > 0) && (
        <div className="card queue-card">
          <div className="row between"><b>Sending</b>{canEdit && <button className="danger" onClick={() => act(async () => { const r = await api<{ reset: number }>('/api/drafts/cancel-sending', 'POST'); setMsg(`Cancelled. ${r.reset} message(s) are back in pending.`); })}>Cancel sending</button>}</div>
          <div className="meterbar" style={{ margin: '10px 0 6px' }}><i style={{ width: `${Math.round((queue.done / Math.max(1, queue.done + queue.remaining)) * 100)}%` }} /></div>
          <small className="muted">{queue.done} sent · {queue.remaining} left{queue.nextAt ? ` · next in ${countdown(queue.nextAt, now)}` : ''}{queue.current ? ` · now: ${queue.current}` : ''}. A random gap between messages keeps your number safe.</small>
        </div>
      )}

      <div className="chips">
        {FILTERS.map(([k, label]) => (
          <button key={k} className={`chipb${filter === k ? ' on' : ''}`} onClick={() => { setFilter(k); setPage(1); setSel(new Set()); }}>{label}{k === 'open' ? <b>{open}</b> : counts[k] !== undefined ? <b>{counts[k]}</b> : null}</button>
        ))}
        <select value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }} aria-label="Message type" style={{ marginLeft: 'auto' }}>
          <option value="">All types</option><option value="first">First messages</option><option value="followup">Follow-ups</option><option value="reply">Replies</option>
        </select>
      </div>

      {canEdit && reviewable.length > 0 && (
        <div className="bulk">
          <label className="chk"><input type="checkbox" checked={reviewable.every((d) => sel.has(d.id))} onChange={(e) => setSel(e.target.checked ? new Set(reviewable.map((d) => d.id)) : new Set())} /> Select all</label>
          <b>{sel.size} selected</b>
          <button className="primary" disabled={!sel.size || dry} title={dry ? 'Turn dry-run off in Setup first' : ''} onClick={() => approve([...sel])}>Approve selected</button>
          <button disabled={!sel.size} onClick={() => confirm(`Discard ${sel.size} message(s)?`) && act(async () => { for (const id of sel) await api(`/api/drafts/${id}/discard`, 'POST'); setSel(new Set()); }, 'Discarded.')}>Discard selected</button>
        </div>
      )}

      <div className="stack">
        {!res && !err && [0, 1].map((i) => <div key={i} className="skeleton" style={{ height: 150 }} />)}
        {res?.rows.map((d) => <DraftCard key={d.id} d={d} canEdit={canEdit} dry={dry} selected={sel.has(d.id)} onSelect={() => toggle(d.id)} act={act} approve={approve} />)}
        {res && !res.rows.length && <div className="card muted" style={{ textAlign: 'center', padding: 32 }}>{filter === 'open' ? 'Nothing to review. When the scheduler or “Run now” drafts messages, they appear here.' : 'No messages in this view.'}</div>}
      </div>
      {res && res.pages > 1 && <div className="row" style={{ justifyContent: 'center', marginTop: 14 }}><button disabled={page <= 1} onClick={() => setPage(page - 1)}>← Prev</button><span className="muted">Page {res.page} of {res.pages} · {num(res.total)} messages</span><button disabled={page >= res.pages} onClick={() => setPage(page + 1)}>Next →</button></div>}
    </div>
  );
}

export function DraftCard({ d, canEdit, dry, selected, onSelect, act, approve, compact }: {
  d: Draft; canEdit: boolean; dry: boolean; selected?: boolean; onSelect?: () => void;
  act: (fn: () => Promise<unknown>, ok?: string) => Promise<void>; approve: (ids: string[]) => Promise<void>; compact?: boolean;
}) {
  const editable = canEdit && (d.status === 'pending' || d.status === 'failed');
  const [text, setText] = useState(d.body);
  const [prev, setPrev] = useState(d.body);
  const [regen, setRegen] = useState(false);
  const [ins, setIns] = useState('');
  const [research, setResearch] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (d.body !== prev) { setPrev(d.body); setText(d.body); }   // a server-side change (regenerate / poll) refreshes the box
  const dirty = text !== d.body;

  async function showResearch() {
    if (research !== null) { setResearch(null); return; }
    try { const r = await api<{ rows: { id: string; research: string }[] }>(`/api/leads?q=${encodeURIComponent(d.whatsapp || d.company)}&page_size=10`); setResearch((r.rows.find((x) => x.id === d.leadId) || r.rows[0])?.research || 'No research saved for this lead yet.'); }
    catch { setResearch('Could not load the research.'); }
  }

  return (
    <div className={`card draft${d.needsHuman ? ' nh' : ''}`}>
      <div className="row between">
        <div className="row" style={{ gap: 10 }}>
          {onSelect && editable && <input type="checkbox" checked={!!selected} onChange={onSelect} aria-label={`Select ${d.company}`} />}
          <div><b>{d.company}</b> <span className="muted">{d.whatsapp ? `+${d.whatsapp}` : ''}</span></div>
        </div>
        <div className="row" style={{ gap: 6 }}>
          <span className="tag">{KIND[d.kind] || d.kind}</span>
          <span className="tag via" title={d.via ? d.via.label : 'The number is chosen when it is sent (least used, under its warm-up cap)'}>{d.via ? `via ${viaText(d.via)}` : 'number chosen at send'}</span>
          {d.needsHuman && <span className="badge bad">NEEDS HUMAN</span>}
          {d.source === 'fallback' && <span className="badge warn">fallback template</span>}
          <span className={`badge ${TONE[d.status] || 'warn'}`}>{d.status}</span>
        </div>
      </div>
      {d.incoming && <div className="quote-in"><small className="muted">They wrote</small>{d.incoming}</div>}
      {d.note && <small className="muted note-line">{d.note}</small>}
      {editable
        ? <textarea className="draft-text" rows={compact ? 3 : 5} value={text} onChange={(e) => setText(e.target.value)} aria-label={`Message to ${d.company}`} />
        : <div className="draft-read">{d.body}</div>}
      {research !== null && <div className="panel2"><b>Research</b><p>{research}</p></div>}
      <div className="row" style={{ marginTop: 4 }}>
        {editable && dirty && <button disabled={busy || !text.trim()} onClick={async () => { setBusy(true); await act(() => api(`/api/drafts/${d.id}`, 'PATCH', { body: text }), 'Saved.'); setBusy(false); }}>Save changes</button>}
        {editable && <button className="primary" disabled={dry || dirty} title={dry ? 'Dry-run is ON: turn it off in Setup' : dirty ? 'Save your changes first' : ''} onClick={() => approve([d.id])}>Approve</button>}
        {editable && <button onClick={() => setRegen(!regen)}>Regenerate…</button>}
        {editable && <button className="danger" onClick={() => act(() => api(`/api/drafts/${d.id}/discard`, 'POST'), 'Discarded.')}>Discard</button>}
        {!compact && d.kind !== 'reply' && <button className="link" onClick={showResearch}>{research !== null ? 'Hide research' : 'Show research'}</button>}
        <small className="muted" style={{ marginLeft: 'auto' }}>{ago(d.createdAt)}</small>
      </div>
      {regen && (
        <form className="row regen" onSubmit={async (e) => { e.preventDefault(); setBusy(true); await act(async () => { await api(`/api/drafts/${d.id}/regenerate`, 'POST', { instruction: ins }); setRegen(false); setIns(''); }, 'Rewritten.'); setBusy(false); }}>
          <input style={{ flex: 1, minWidth: 200 }} autoFocus placeholder="How should it change? e.g. shorter, mention their new branch, more formal" value={ins} onChange={(e) => setIns(e.target.value)} maxLength={300} />
          <button className="primary" disabled={busy}>{busy ? 'Writing…' : 'Rewrite'}</button>
        </form>
      )}
    </div>
  );
}
