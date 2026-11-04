import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';

type Endpoint = { id: string; url: string; description: string; events: string[]; active: boolean; createdAt: string };
type Delivery = { id: string; endpointId: string; event: string; status: string; attempts: number; lastStatus: number | null; error: string; createdAt: string; nextAttemptAt: string | null };

const ST: Record<string, [string, string]> = { success: ['Delivered', 'ok'], pending: ['Queued', 'warn'], retrying: ['Retrying', 'warn'], failed: ['Failed', 'bad'] };
const when = (iso: string) => new Date(iso).toLocaleString();

export function Webhooks() {
  const [eps, setEps] = useState<Endpoint[]>([]);
  const [all, setAll] = useState<string[]>([]);
  const [dels, setDels] = useState<Delivery[]>([]);
  const [url, setUrl] = useState('');
  const [desc, setDesc] = useState('');
  const [picked, setPicked] = useState<string[]>([]);
  const [secret, setSecret] = useState<{ id: string; value: string } | null>(null);
  const [err, setErr] = useState('');
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api<{ endpoints: Endpoint[]; events: string[] }>('/api/webhooks');
      setEps(r.endpoints); setAll(r.events); setPicked((p) => (p.length ? p : r.events));
      setDels(await api<Delivery[]>('/api/webhooks/deliveries'));
    } catch (e: any) { setErr(e.message); }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);

  async function create() {
    setErr('');
    try {
      const r = await api<Endpoint & { secret: string }>('/api/webhooks', 'POST', { url, description: desc, events: picked });
      setSecret({ id: r.id, value: r.secret }); setCopied(false); setUrl(''); setDesc(''); load();
    } catch (e: any) { setErr(e.message); }
  }
  const act = async (fn: () => Promise<unknown>) => { setErr(''); try { await fn(); load(); } catch (e: any) { setErr(e.message); } };
  const toggle = (e: string) => setPicked((p) => (p.includes(e) ? p.filter((x) => x !== e) : [...p, e]));

  return (
    <section>
      <h2>Webhooks</h2>
      <p className="muted">ConnectFlow calls your URL when something happens (a reply arrives, a number connects or disconnects). Each request is signed so you can verify it came from us. Failed deliveries are retried automatically.</p>
      {err && <div className="err">{err}</div>}

      <div className="card stack">
        <b>Add an endpoint</b>
        <div className="row">
          <input style={{ flex: 2, minWidth: 220 }} placeholder="https://your-app.com/hooks/connectflow" value={url} onChange={(e) => setUrl(e.target.value)} />
          <input style={{ flex: 1, minWidth: 140 }} placeholder="Description (optional)" value={desc} onChange={(e) => setDesc(e.target.value)} />
        </div>
        <div className="row">
          {all.map((e) => <label key={e} className="chk"><input type="checkbox" checked={picked.includes(e)} onChange={() => toggle(e)} /> <code>{e}</code></label>)}
          <button className="primary" style={{ marginLeft: 'auto' }} onClick={create} disabled={!url || !picked.length}>Add endpoint</button>
        </div>
      </div>

      {secret && (
        <div className="card newkey">
          <b>Signing secret — copy it now, it won’t be shown again.</b>
          <div className="keybox"><code>{secret.value}</code><button onClick={async () => { await navigator.clipboard.writeText(secret.value); setCopied(true); }}>{copied ? 'Copied ✓' : 'Copy'}</button></div>
          <small className="muted">Verify each request: HMAC-SHA256 of <code>timestamp + "." + rawBody</code> with this secret must equal the <code>X-ConnectFlow-Signature</code> header.</small>
          <button className="link" onClick={() => setSecret(null)}>I’ve saved it</button>
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: '1fr', marginTop: 16 }}>
        {eps.map((e) => (
          <div className="card" key={e.id} style={{ opacity: e.active ? 1 : 0.6 }}>
            <div className="row between">
              <div><b style={{ wordBreak: 'break-all' }}>{e.url}</b><div className="muted">{e.description || 'No description'} · {e.events.join(', ')}</div></div>
              <div className="row">
                <button onClick={() => act(() => api(`/api/webhooks/${e.id}/test`, 'POST'))}>Send test event</button>
                <button onClick={() => act(() => api(`/api/webhooks/${e.id}`, 'PATCH', { active: !e.active }))}>{e.active ? 'Pause' : 'Resume'}</button>
                <button onClick={() => act(async () => { const r = await api<{ secret: string }>(`/api/webhooks/${e.id}/rotate-secret`, 'POST'); setSecret({ id: e.id, value: r.secret }); setCopied(false); })}>Rotate secret</button>
                <button className="danger" onClick={() => confirm('Delete this endpoint?') && act(() => api(`/api/webhooks/${e.id}`, 'DELETE'))}>Delete</button>
              </div>
            </div>
          </div>
        ))}
        {!eps.length && <div className="card muted">No endpoints yet. Add one above, then press “Send test event”.</div>}
      </div>

      <h3 style={{ marginTop: 28 }}>Recent deliveries</h3>
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Event</th><th>Status</th><th>Attempts</th><th>Response</th><th>When</th><th></th></tr></thead>
          <tbody>
            {dels.map((d) => { const [t, c] = ST[d.status] || [d.status, 'warn']; return (
              <tr key={d.id}>
                <td><code>{d.event}</code></td>
                <td><span className={`badge ${c}`}>{t}</span></td>
                <td>{d.attempts}</td>
                <td className="muted">{d.lastStatus ? `HTTP ${d.lastStatus}` : d.error || '—'}{d.nextAttemptAt && ` · next ${when(d.nextAttemptAt)}`}</td>
                <td className="muted">{when(d.createdAt)}</td>
                <td>{(d.status === 'failed' || d.status === 'retrying') && <button onClick={() => act(() => api(`/api/webhooks/deliveries/${d.id}/retry`, 'POST'))}>Retry now</button>}</td>
              </tr>); })}
            {!dels.length && <tr><td colSpan={6} className="muted">Nothing delivered yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
