import { useState } from 'react';
import { api } from '../api';
import { Badge, Confirm, Empty, ErrorBox, Kpi, Loading, When, num, useGet } from './ui';

type R = { id: string; tenant: string; tenantId: string; currentPlan: string; requestedPlan: string; priceUsd: number; pricePkr: number; note: string; status: string; createdAt: string };
export function Requests({ openCustomer, onChange }: { openCustomer: (id: string) => void; onChange: () => void }) {
  const { data, err, loading, reload, setErr } = useGet<R[]>('/api/admin/upgrade-requests');
  const [ask, setAsk] = useState<{ r: R; what: 'approve' | 'reject' } | null>(null);
  const run = async () => { if (!ask) return; try { await api(`/api/admin/upgrade-requests/${ask.r.id}/${ask.what}`, 'POST'); setAsk(null); reload(); onChange(); } catch (e: any) { setErr(e.message); setAsk(null); } };
  return (
    <div>
      <div className="ad-head"><div><h2>Plan requests</h2><p>Approve only after the payment has arrived. The customer is emailed automatically.</p></div></div>
      <ErrorBox msg={err} />
      <div className="ad-tw"><table className="ad-t"><thead><tr><th>Customer</th><th>From → To</th><th>Price</th><th>Note</th><th>Status</th><th>Requested</th><th></th></tr></thead><tbody>
        {data?.map((r) => <tr key={r.id}><td><a href="#customers" onClick={(e) => { e.preventDefault(); openCustomer(r.tenantId); }}>{r.tenant}</a></td><td>{r.currentPlan} → <b>{r.requestedPlan}</b></td><td>${r.priceUsd} / PKR {num(r.pricePkr)}</td><td className="wrap">{r.note || '—'}</td><td><Badge v={r.status} /></td><td><When iso={r.createdAt} /></td>
          <td>{r.status === 'pending' && <span className="ad-row"><button className="ad-btn sm pri" onClick={() => setAsk({ r, what: 'approve' })}>Payment received · Approve</button><button className="ad-btn sm" onClick={() => setAsk({ r, what: 'reject' })}>Reject</button></span>}</td></tr>)}
      </tbody></table>{loading && !data && <Loading />}{data && !data.length && <Empty>No plan requests.</Empty>}</div>
      {ask && <Confirm title={`${ask.what === 'approve' ? 'Approve' : 'Reject'} ${ask.r.tenant} → ${ask.r.requestedPlan}?`} danger={ask.what === 'reject'} confirmLabel={ask.what === 'approve' ? 'Approve' : 'Reject'} onCancel={() => setAsk(null)} onConfirm={run}>
        {ask.what === 'approve' ? 'The plan changes immediately and the owner gets an email.' : 'The owner is told the request could not be completed.'}</Confirm>}
    </div>
  );
}

type P = { id: string; name: string; priceUsd: number; pricePkr: number; maxInstances: number; maxMsgsDay: number; maxMembers: number; aiCallsDay: number };
export function Plans() {
  const { data, err, loading, reload, setErr } = useGet<P[]>('/api/admin/plans');
  const [draft, setDraft] = useState<Record<string, P>>({});
  const [ok, setOk] = useState('');
  const val = (p: P) => draft[p.id] || p;
  const set = (p: P, k: keyof P, v: number | string) => setDraft({ ...draft, [p.id]: { ...val(p), [k]: v } });
  const save = async (p: P) => { setOk(''); try { await api(`/api/admin/plans/${p.id}`, 'PUT', val(p)); const n = { ...draft }; delete n[p.id]; setDraft(n); setOk(`${p.name} saved.`); reload(); } catch (e: any) { setErr(e.message); } };
  const f: [keyof P, string][] = [['priceUsd', 'USD / mo'], ['pricePkr', 'PKR / mo'], ['maxInstances', 'Numbers'], ['maxMsgsDay', 'Msgs / day'], ['maxMembers', 'Seats'], ['aiCallsDay', 'AI calls / day']];
  return (
    <div>
      <div className="ad-head"><div><h2>Plans</h2><p>Limits and prices apply to everyone on the plan immediately.</p></div></div>
      <ErrorBox msg={err} />{ok && <div className="ad-badge ok" style={{ marginBottom: 10 }}>{ok}</div>}
      <div className="ad-tw"><table className="ad-t"><thead><tr><th>Plan</th>{f.map(([, l]) => <th key={l}>{l}</th>)}<th></th></tr></thead><tbody>
        {data?.map((p) => <tr key={p.id}><td><b>{p.name}</b> <span className="ad-faint">{p.id}</span></td>{f.map(([k]) => <td key={k}><input className="ad-in" type="number" min={0} style={{ width: 96 }} value={val(p)[k] as number} onChange={(e) => set(p, k, Number(e.target.value))} /></td>)}<td><button className="ad-btn sm pri" disabled={!draft[p.id]} onClick={() => save(p)}>Save</button></td></tr>)}
      </tbody></table>{loading && !data && <Loading />}</div>
    </div>
  );
}


type Cap = {
  gateway: { sessions: number; maxSessions: number; ready: number; qrPending: number; memMb: number };
  db: { sizeBytes: number; tables: { name: string; rows: number; sizeBytes?: number }[] };
  retention: { lastRunAt: string | null; deleted: Record<string, number> | number };
  backups: { lastBackupAt: string | null; sizeBytes: number };
  ai: { callsToday: number; globalCap: number; breakerOpen: boolean };
};
const mb = (b: number) => (b >= 1073741824 ? `${(b / 1073741824).toFixed(1)} GB` : `${(b / 1048576).toFixed(1)} MB`);
/** Capacity block. The endpoint ships separately: a 404 just means "not available yet". */
function Capacity() {
  const { data, err, loading } = useGet<Cap>('/api/admin/capacity');
  const missing = /not found|404/i.test(err);
  return (
    <div style={{ marginTop: 22 }}>
      <div className="ad-head"><div><h2 style={{ fontSize: 16 }}>Capacity</h2><p>Gateway sessions, database, retention, backups and AI budget.</p></div></div>
      {loading && !data && <Loading />}
      {missing && <div className="ad-note">Capacity metrics are not available yet.</div>}
      {err && !missing && <ErrorBox msg={err} />}
      {data && <div className="ad-grid ad-kpis">
        <Kpi v={`${num(data.gateway?.sessions)} / ${data.gateway?.maxSessions || '∞'}`} l="Gateway sessions" s={`${num(data.gateway?.ready)} ready · ${num(data.gateway?.qrPending)} waiting for QR · ${num(data.gateway?.memMb)} MB`} />
        <Kpi v={mb(data.db?.sizeBytes || 0)} l="Database size" s={`${data.db?.tables?.length || 0} tables`} />
        <Kpi v={<When iso={data.retention?.lastRunAt} />} l="Retention last run" s={typeof data.retention?.deleted === 'number' ? `${num(data.retention.deleted)} rows removed` : `${num(Object.values(data.retention?.deleted || {}).reduce((a, b) => a + b, 0))} rows removed`} />
        <Kpi v={<When iso={data.backups?.lastBackupAt} />} l="Last backup" s={data.backups?.sizeBytes ? mb(data.backups.sizeBytes) : undefined} />
        <Kpi v={data.ai?.breakerOpen ? <Badge v="breaker open" tone="bad" /> : `${num(data.ai?.callsToday)}${data.ai?.globalCap ? ` / ${num(data.ai.globalCap)}` : ''}`} l="AI calls today" s={data.ai?.globalCap ? 'platform-wide cap' : 'no platform cap set'} />
      </div>}
    </div>
  );
}

type H = { database: string; redis: string; gateway: { ok: boolean; error?: string }; worker: { pending: number; retrying: number; failed24h: number }; time: string };
export function System() {
  const { data, err, loading, reload } = useGet<H>('/api/admin/health');
  return (
    <div>
      <div className="ad-head"><div><h2>System</h2><p>Live health of the services behind the platform.</p></div><button className="ad-btn" onClick={reload}>Re-check</button></div>
      <ErrorBox msg={err} />{loading && !data && <Loading />}
      {data && <div className="ad-grid ad-kpis">
        <Kpi v={<Badge v={data.database} />} l="Database" /><Kpi v={<Badge v={data.redis} />} l="Redis" /><Kpi v={<Badge v={data.gateway.ok ? 'ok' : 'error'} />} l="WhatsApp gateway" s={data.gateway.error} />
        <Kpi v={num(data.worker.pending)} l="Webhooks pending" /><Kpi v={num(data.worker.retrying)} l="Webhooks retrying" /><Kpi v={num(data.worker.failed24h)} l="Webhooks failed (24 h)" s={`checked ${new Date(data.time).toLocaleTimeString()}`} />
      </div>}
      <Capacity />
    </div>
  );
}
