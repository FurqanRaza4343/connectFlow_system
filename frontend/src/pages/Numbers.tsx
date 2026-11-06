import { useCallback, useEffect, useRef, useState } from 'react';
import { api, Instance, Me } from '../api';

type Stat = { id: string; sentToday: number; sent7d: number; replies7d: number; warmupCap: number; outreachEnabled: boolean };

const LABEL: Record<string, [string, string]> = {
  ready: ['Connected', 'ok'], qr_ready: ['Scan QR', 'warn'], initializing: ['Starting…', 'warn'], authenticating: ['Linking…', 'warn'],
  created: ['Not started', 'bad'], disconnected: ['Disconnected', 'bad'], failed: ['Failed', 'bad'],
};
const badge = (s: string) => { const [t, c] = LABEL[s] || [s, 'warn']; return <span className={`badge ${c}`}>{t}</span>; };

export function Numbers({ me }: { me: Me }) {
  const [list, setList] = useState<Instance[]>([]);
  const [err, setErr] = useState('');
  const [label, setLabel] = useState('');
  const [scan, setScan] = useState<Instance | null>(null);
  const [stats, setStats] = useState<Record<string, Stat>>({});
  const canManage = ['owner', 'admin'].includes(me.role);
  const limit = me.tenant!.plan.maxInstances;

  const load = useCallback(() => {
    api<Instance[]>('/api/instances').then(setList).catch((e) => setErr(e.message));
    api<{ instances: Stat[] }>('/api/outreach/status').then((s) => setStats(Object.fromEntries(s.instances.map((x) => [x.id, x])))).catch(() => { /* counters are optional */ });
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);

  async function add() {
    setErr('');
    try { const i = await api<Instance>('/api/instances', 'POST', { label: label || 'My WhatsApp number' }); setLabel(''); await load(); setScan(i); }
    catch (e: any) { setErr(e.message); }
  }
  async function toggle(i: Instance) {
    setErr('');
    try { await api(`/api/instances/${i.id}`, 'PATCH', { outreach_enabled: !(i.outreachEnabled ?? true) }); load(); } catch (e: any) { setErr(e.message); }
  }
  async function remove(i: Instance) {
    if (!confirm(`Remove "${i.label}"? The WhatsApp link will be deleted.`)) return;
    try { await api(`/api/instances/${i.id}`, 'DELETE'); load(); } catch (e: any) { setErr(e.message); }
  }

  return (
    <section>
      <div className="row between">
        <div><h2>WhatsApp numbers</h2><p className="muted">{list.length} of {limit} number{limit > 1 ? 's' : ''} used on your {me.tenant!.plan.name} plan</p>
          <div className="meterbar num-meter" aria-hidden="true"><i style={{ width: `${Math.min(100, (list.length / Math.max(1, limit)) * 100)}%` }} className={list.length >= limit ? 'hot' : ''} /></div></div>
        {canManage && (
          <div className="row">
            <input placeholder="Label, e.g. Sales team" value={label} onChange={(e) => setLabel(e.target.value)} />
            <button className="primary" onClick={add} disabled={list.length >= limit} title={list.length >= limit ? `Your ${me.tenant!.plan.name} plan includes ${limit} number${limit > 1 ? 's' : ''}` : undefined}>+ Add number</button>
          </div>
        )}
      </div>
      {err && <div className="err">{err}</div>}
      {list.length >= limit && (
        <div className="card upsell">
          <div><b>{me.tenant!.plan.name === 'Trial' ? 'Your free Trial includes 1 WhatsApp number.' : `Your ${me.tenant!.plan.name} plan is full.`}</b>
            <p className="muted">Need more? Starter has 3 numbers and 500 messages a day; Growth has 10 numbers and 3,000 messages a day.</p></div>
          {me.role === 'owner' ? <a className="btn-link primary" href="#billing">See plans &amp; upgrade</a> : <small className="muted">Ask the workspace owner to upgrade.</small>}
        </div>
      )}
      <div className="grid">
        {list.map((i) => (
          <div className="card" key={i.id}>
            <div className="row between"><b>{i.label}</b>{i.overLimit ? <span className="badge bad" title="Your plan was downgraded below the number of linked numbers. The number is kept but paused.">Over plan limit</span> : badge(i.status)}</div>
            <p className="muted">{i.phone ? `+${i.phone.replace(/\D/g, '')}` : 'No number linked yet'}</p>
            {stats[i.id] && <div className="num-stats"><span><b>{stats[i.id].sentToday}</b>/{stats[i.id].warmupCap}<small>today (warm-up cap)</small></span><span><b>{stats[i.id].sent7d}</b><small>sent · 7 days</small></span><span><b>{stats[i.id].replies7d}</b><small>replies · 7 days</small></span></div>}
            <label className="switch-row num-switch"><span><b>Use for outreach</b><small className="muted">{(i.outreachEnabled ?? true) ? 'The AI sender may use this number.' : 'API-only: the outreach sender never picks it.'}</small></span>
              <input type="checkbox" role="switch" checked={i.outreachEnabled ?? true} disabled={!canManage} onChange={() => toggle(i)} aria-label={`Use ${i.label} for outreach`} /></label>
            <div className="row">
              {i.status !== 'ready' && <button onClick={() => setScan(i)}>Connect / scan QR</button>}
              {canManage && <button className="danger" onClick={() => remove(i)}>Remove</button>}
            </div>
          </div>
        ))}
        {!list.length && <div className="card muted">No numbers yet. Click “Add number”, then scan the QR with WhatsApp → Linked devices.</div>}
      </div>
      {scan && <QrModal inst={scan} onClose={() => { setScan(null); load(); }} />}
    </section>
  );
}

function QrModal({ inst, onClose }: { inst: Instance; onClose: () => void }) {
  const [qr, setQr] = useState<string | null>(null);
  const [status, setStatus] = useState(inst.status);
  const [err, setErr] = useState('');
  const done = useRef(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await api<{ status: string; qr: string | null }>(`/api/instances/${inst.id}/qr`);
        if (!alive) return;
        setStatus(r.status); setQr(r.qr); setErr('');
        if (r.status === 'ready') done.current = true;
      } catch (e: any) { if (alive) setErr(e.message); }
    };
    tick();
    const t = setInterval(() => { if (!done.current) tick(); }, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [inst.id]);

  return (
    <div className="modal" onClick={onClose}>
      <div className="card qr" onClick={(e) => e.stopPropagation()}>
        <h3>Link “{inst.label}”</h3>
        {status === 'ready' ? <p className="ok-text">✅ Connected! You can close this window.</p>
          : qr ? <><img alt="WhatsApp QR code" src={qr} /><p className="muted">WhatsApp → Settings → Linked devices → Link a device. The QR refreshes by itself.</p></>
          : <p className="muted">Preparing QR… ({status})</p>}
        {err && <div className="err">{err}</div>}
        <button onClick={onClose}>Close</button>
      </div>
    </div>
  );
}
