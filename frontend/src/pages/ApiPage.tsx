import { useCallback, useEffect, useState } from 'react';
import { api, Instance, Me } from '../api';
import { Modal } from '../components/Modal';

type Key = { id: string; name: string; prefix: string; instanceId: string | null; createdAt: string; lastUsedAt: string | null; revoked: boolean; expiresAt: string | null; expired: boolean;
  status: 'active' | 'suspended' | 'expired' | 'revoked'; allowedIps: string; lastIp: string; lastUserAgent: string; requestsToday: number; suspendedReason: string; suspendedBy: string };
const EXPIRY: Array<[string, string]> = [['1h', '1 hour'], ['1d', '1 day'], ['7d', '7 days'], ['30d', '30 days (1 month)'], ['90d', '90 days'], ['1y', '1 year'], ['never', 'No expiration']];
type Usage = { today: number; limit: number; plan: string; days: { day: string; count: number }[] };

const ago = (iso: string | null) => {
  if (!iso) return 'never';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : `${Math.floor(s / 86400)} d ago`;
};

export function ApiPage({ me }: { me: Me }) {
  const [keys, setKeys] = useState<Key[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [numbers, setNumbers] = useState<Instance[]>([]);
  const [dlg, setDlg] = useState(false);
  const [name, setName] = useState('');
  const [scope, setScope] = useState('');
  const [exp, setExp] = useState('90d');
  const [ips, setIps] = useState('');
  const [edit, setEdit] = useState<Key | null>(null);
  const [editIps, setEditIps] = useState('');
  const [fresh, setFresh] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState('');
  const canManage = ['owner', 'admin'].includes(me.role);
  const origin = location.origin;

  const load = useCallback(async () => {
    try {
      const [u, n] = await Promise.all([api<Usage>('/api/usage'), api<Instance[]>('/api/instances')]);
      setUsage(u); setNumbers(n);
      if (canManage) setKeys(await api<Key[]>('/api/keys'));
    } catch (e: any) { setErr(e.message); }
  }, [canManage]);
  useEffect(() => { load(); }, [load]);

  async function create() {
    setErr('');
    try {
      const r = await api<Key & { key: string }>('/api/keys', 'POST', { name: name.trim(), instance_id: scope || null, expires_in: exp, allowed_ips: ips });
      setFresh(r.key); setName(''); setScope(''); setExp('90d'); setIps(''); setCopied(false); setDlg(false); load();
    } catch (e: any) { setErr(e.message); }
  }
  async function revoke(k: Key) {
    if (!confirm(`Revoke "${k.name}"? Apps using it will stop working immediately.`)) return;
    try { await api(`/api/keys/${k.id}`, 'DELETE'); load(); } catch (e: any) { setErr(e.message); }
  }
  async function remove(k: Key) {
    if (!confirm(`Delete "${k.name}" permanently? ${k.revoked || k.expired ? 'It is already inactive.' : 'Apps using it will stop working immediately.'}`)) return;
    try { await api(`/api/keys/${k.id}?permanent=true`, 'DELETE'); load(); } catch (e: any) { setErr(e.message); }
  }
  async function unsuspend(k: Key) {
    try { await api(`/api/keys/${k.id}/unsuspend`, 'POST'); load(); } catch (e: any) { setErr(e.message); }
  }
  async function rotate(k: Key) {
    if (!confirm(`Rotate "${k.name}"? You get a new key now and the old one stops working immediately.`)) return;
    try { const r = await api<Key & { key: string }>(`/api/keys/${k.id}/rotate`, 'POST'); setFresh(r.key); setCopied(false); load(); } catch (e: any) { setErr(e.message); }
  }
  async function saveIps() {
    if (!edit) return;
    try { await api(`/api/keys/${edit.id}`, 'PATCH', { allowed_ips: editIps }); setEdit(null); load(); } catch (e: any) { setErr(e.message); }
  }
  const copy = async () => { if (fresh) { await navigator.clipboard.writeText(fresh); setCopied(true); } };

  const max = Math.max(1, ...(usage?.days.map((d) => d.count) || [1]), usage?.limit ? 0 : 0);
  const pct = usage ? Math.min(100, Math.round((usage.today / Math.max(1, usage.limit)) * 100)) : 0;
  const sample = `curl -X POST ${origin}/v1/messages \\\n  -H "Authorization: Bearer ${fresh || 'cf_live_…'}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"instance":"${numbers[0]?.id || 'INSTANCE_ID'}","to":"923001234567","text":"Hello from ConnectFlow"}'`;

  return (
    <section>
      <h2>API &amp; usage</h2>
      <p className="muted">Send messages from your own systems. Keys act on your workspace only, and follow the same limits as the dashboard.</p>
      {err && <div className="err">{err}</div>}

      {usage && (
        <div className="card usage">
          <div className="row between"><b>Messages today</b><span className="muted">{usage.today} / {usage.limit} · {usage.plan} plan</span></div>
          <div className="meterbar"><i style={{ width: `${pct}%` }} className={pct > 85 ? 'hot' : ''} /></div>
          <div className="spark" role="img" aria-label="Messages in the last 7 days">
            {usage.days.map((d) => (
              <div key={d.day} title={`${d.day}: ${d.count}`}><i style={{ height: `${Math.max(4, (d.count / max) * 100)}%` }} /><span>{d.day.slice(8)}</span></div>
            ))}
          </div>
        </div>
      )}

      {canManage ? (
        <>
          <div className="row between" style={{ marginTop: 28 }}>
            <h3 style={{ margin: 0 }}>API keys</h3>
            <button className="primary" onClick={() => setDlg(true)}>+ Create API key</button>
          </div>

          {fresh && (
            <div className="card newkey">
              <b>Copy your key now — it won’t be shown again.</b>
              <div className="keybox"><code>{fresh}</code><button onClick={copy}>{copied ? 'Copied ✓' : 'Copy'}</button></div>
              <button className="link" onClick={() => setFresh(null)}>I’ve saved it</button>
            </div>
          )}

          <div className="card table-wrap" style={{ marginTop: 12 }}>
            <table>
              <thead><tr><th>Name</th><th>Key</th><th>Status</th><th>Scope</th><th>Expires</th><th>Last used</th><th>Last IP</th><th>Today</th><th></th></tr></thead>
              <tbody>
                {keys.map((k) => (
                  <tr key={k.id} style={{ opacity: k.revoked || k.expired ? 0.5 : 1 }}>
                    <td>{k.name}</td><td><code>{k.prefix}…</code></td>
                    <td>{k.status === 'suspended' ? <span className="badge bad" title={k.suspendedReason}>suspended</span> : k.status === 'active' ? <span className="badge ok">active</span> : <span className="badge bad">{k.status}</span>}
                      {k.status === 'suspended' && k.suspendedReason && <div className="muted" style={{ fontSize: 12, maxWidth: 220 }}>{k.suspendedBy === 'admin' ? 'By ConnectFlow: ' : ''}{k.suspendedReason}</div>}
                      {k.allowedIps && <div className="muted" style={{ fontSize: 12 }}>IP allow-list on</div>}</td>
                    <td>{k.instanceId ? numbers.find((n) => n.id === k.instanceId)?.label || 'One number' : 'All numbers'}</td>
                    <td>{k.expiresAt ? <span className={k.expired ? 'badge bad' : 'muted'}>{k.expired ? 'expired' : new Date(k.expiresAt).toLocaleString()}</span> : <span className="muted">never</span>}</td>
                    <td>{ago(k.lastUsedAt)}</td>
                    <td title={k.lastUserAgent}>{k.lastIp ? <code>{k.lastIp}</code> : <span className="muted">—</span>}</td>
                    <td>{k.requestsToday}</td>
                    <td><span className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                      {k.status === 'suspended' && k.suspendedBy !== 'admin' && <button className="primary" onClick={() => unsuspend(k)}>Unsuspend</button>}
                      {!k.revoked && !k.expired && <button onClick={() => { setEdit(k); setEditIps(k.allowedIps.split(',').map((x) => x.trim()).filter(Boolean).join(', ')); }}>IPs</button>}
                      {!k.revoked && <button onClick={() => rotate(k)}>Rotate</button>}
                      {k.revoked ? <span className="badge bad">revoked</span> : !k.expired && <button onClick={() => revoke(k)}>Revoke</button>}<button className="danger" onClick={() => remove(k)}>Delete</button></span></td>
                  </tr>
                ))}
                {!keys.length && <tr><td colSpan={9} className="muted">No keys yet.</td></tr>}
              </tbody>
            </table>
          </div>

          <h3 style={{ marginTop: 28 }}>Send your first message</h3>
          <pre className="codeblock">{sample}</pre>
          <p className="muted">Responses: <code>202</code> sent · <code>401</code> bad key · <code>409</code> number not connected · <code>422</code> invalid / not on WhatsApp · <code>429</code> daily limit or rate limit (see <code>Retry-After</code>) · <code>403</code> IP not on the key’s allow-list. A key used from many different IPs is suspended automatically and you are emailed.</p>
        </>
      ) : (
        <p className="muted" style={{ marginTop: 24 }}>Only workspace owners and admins can manage API keys.</p>
      )}
      {dlg && (
        <Modal title="Create API key" onClose={() => setDlg(false)}>
          <form className="stack" onSubmit={(e) => { e.preventDefault(); create(); }}>
            <label>Name<input autoFocus required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. CRM sync, Shopify orders" /></label>
            <label>Expires<select value={exp} onChange={(e) => setExp(e.target.value)}>{EXPIRY.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            {exp === 'never' && <small className="muted">A key that never expires is convenient but riskier. Prefer a limit, and rotate regularly.</small>}
            <label>Access<select value={scope} onChange={(e) => setScope(e.target.value)}><option value="">All numbers</option>{numbers.map((n) => <option key={n.id} value={n.id}>Only “{n.label}”</option>)}</select></label>
            <label>Allowed IP addresses <small className="muted">(optional)</small><textarea rows={2} value={ips} onChange={(e) => setIps(e.target.value)} placeholder="203.0.113.7, 198.51.100.0/24, 2001:db8::/32" /></label>
            <small className="muted">Leave empty to accept any IP. With a list, requests from other addresses are refused (403).</small>
            <div className="row between"><button type="button" onClick={() => setDlg(false)}>Cancel</button><button className="primary" disabled={!name.trim()}>Create key</button></div>
          </form>
        </Modal>
      )}
      {edit && (
        <Modal title={`Allowed IPs · ${edit.name}`} onClose={() => setEdit(null)}>
          <form className="stack" onSubmit={(e) => { e.preventDefault(); saveIps(); }}>
            <label>One per line or comma separated (IPv4, IPv6 or CIDR)<textarea rows={4} value={editIps} onChange={(e) => setEditIps(e.target.value)} placeholder="203.0.113.7&#10;198.51.100.0/24" /></label>
            <small className="muted">Empty = any IP can use this key. Your server’s public IP is the one to list.</small>
            <div className="row between"><button type="button" onClick={() => setEdit(null)}>Cancel</button><button className="primary">Save</button></div>
          </form>
        </Modal>
      )}

    </section>
  );
}
