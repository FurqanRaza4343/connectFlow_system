import { useState } from 'react';
import { api } from '../api';
import { Badge, Confirm, Empty, ErrorBox, Kpi, Loading, Pager, When, num, useGet } from './ui';

type Ov = {
  events24h: { critical: number; warn: number; info: number };
  open: { critical: number; warn: number; info: number };
  suspendedKeys: number;
  flaggedKeys: { id: string; name: string; prefix: string; tenantId: string; tenant: string; suspended: boolean; suspendedReason: string; suspendedBy: string; lastIp: string; requestsToday: number; distinctIpsToday: number }[];
  topKeys: { keyId: string; name: string; prefix: string; tenant: string; requests: number; errors: number; distinctIps: number; lastIp: string }[];
  authStorms: { ip: string; failures: number; retryIn: number }[];
};
type Ev = { id: number; at: string; severity: string; kind: string; detail: string; resolved: boolean; tenantId: string | null; tenant: string | null; keyId: string | null; keyName: string | null; keyPrefix: string | null; keySuspended: boolean };
type EvPage = { rows: Ev[]; total: number; page: number; pages: number; kinds: string[] };

const tone = (s: string) => (s === 'critical' ? 'bad' : s === 'warn' ? 'warn' : 'info');
const KIND: Record<string, string> = {
  many_ips: 'Many IPs', new_ip: 'New IP', volume_spike: 'Volume spike', auth_storm: '401 storm', dead_key_used: 'Dead key used', ip_blocked: 'IP not allowed',
  off_hours_send: 'Off-hours send', admin_suspend: 'Admin suspended', admin_unsuspend: 'Admin unsuspended', tenant_lock: 'Workspace locked', ai_breaker: 'AI breaker',
};

export function Security({ openCustomer }: { openCustomer: (id: string) => void }) {
  const ov = useGet<Ov>('/api/admin/security/overview');
  const [sev, setSev] = useState(''); const [kind, setKind] = useState(''); const [res, setRes] = useState('false'); const [page, setPage] = useState(1);
  const q = `/api/admin/security/events?page=${page}${sev ? `&severity=${sev}` : ''}${kind ? `&kind=${kind}` : ''}${res ? `&resolved=${res}` : ''}`;
  const ev = useGet<EvPage>(q);
  const [err, setErr] = useState('');
  const [ask, setAsk] = useState<{ what: 'suspend' | 'lock'; id: string; label: string } | null>(null);
  const refresh = () => { ov.reload(); ev.reload(); };
  const act = async (path: string, body?: unknown) => { setErr(''); try { await api(path, 'POST', body); refresh(); } catch (e: any) { setErr(e.message); } };
  const o = ov.data;

  return (
    <div>
      <div className="ad-head"><div><h2>Security</h2><p>API-key usage, suspicious activity and kill switches. Keys are never shown in full: only a hash is stored.</p></div><button className="ad-btn" onClick={refresh}>Refresh</button></div>
      <ErrorBox msg={ov.err || ev.err || err} />{ov.loading && !o && <Loading />}
      {o && <>
        <div className="ad-grid ad-kpis">
          <Kpi v={<span style={{ color: o.open.critical ? 'var(--bad)' : undefined }}>{num(o.open.critical)}</span>} l="Open critical" s={`${num(o.events24h.critical)} in 24 h`} />
          <Kpi v={num(o.open.warn)} l="Open warnings" s={`${num(o.events24h.warn)} in 24 h`} />
          <Kpi v={num(o.suspendedKeys)} l="Suspended keys" />
          <Kpi v={num(o.authStorms.length)} l="IPs failing auth now" s="5+ wrong keys in 5 min" />
        </div>

        <div className="ad-grid ad-2" style={{ marginTop: 14 }}>
          <div className="ad-card" style={{ overflowX: 'auto' }}><h3>Flagged keys</h3>
            {o.flaggedKeys.length ? <table className="ad-t"><thead><tr><th>Key</th><th>Customer</th><th>State</th><th>Today</th><th>IPs</th><th></th></tr></thead><tbody>
              {o.flaggedKeys.map((k) => <tr key={k.id}><td>{k.name}<div className="ad-faint"><code>{k.prefix}…</code> · {k.lastIp || 'no IP yet'}</div></td>
                <td><a href="#customers" onClick={(e) => { e.preventDefault(); openCustomer(k.tenantId); }}>{k.tenant}</a></td>
                <td>{k.suspended ? <Badge v="suspended" /> : <Badge v="watch" tone="warn" />}{k.suspendedReason && <div className="ad-faint wrap" style={{ maxWidth: 200 }}>{k.suspendedBy === 'admin' ? 'by admin: ' : ''}{k.suspendedReason}</div>}</td>
                <td>{num(k.requestsToday)}</td><td>{k.distinctIpsToday}</td>
                <td>{k.suspended ? <button className="ad-btn sm" onClick={() => act(`/api/admin/security/keys/${k.id}/unsuspend`)}>Unsuspend</button> : <button className="ad-btn sm bad" onClick={() => setAsk({ what: 'suspend', id: k.id, label: `${k.name} (${k.tenant})` })}>Suspend</button>}</td></tr>)}
            </tbody></table> : <Empty>No flagged keys.</Empty>}
          </div>
          <div className="ad-card"><h3>Failed-auth storms (live)</h3>
            {o.authStorms.length ? <table className="ad-t"><thead><tr><th>IP</th><th>Failures</th><th>Blocked for</th></tr></thead><tbody>{o.authStorms.map((s) => <tr key={s.ip}><td className="ad-mono">{s.ip}</td><td>{num(s.failures)}</td><td>{s.retryIn > 0 ? `${Math.ceil(s.retryIn / 60)} min` : '—'}</td></tr>)}</tbody></table> : <Empty>No IP is failing authentication right now.</Empty>}
            <div className="ad-note">An address is blocked for 5 minutes after 20 wrong keys. Counters live in Redis and expire on their own.</div>
          </div>
        </div>

        <div className="ad-card" style={{ marginTop: 14, overflowX: 'auto' }}><h3>Top keys today</h3>
          {o.topKeys.length ? <table className="ad-t"><thead><tr><th>Key</th><th>Customer</th><th>Requests</th><th>Errors</th><th>Distinct IPs</th><th>Last IP</th></tr></thead><tbody>
            {o.topKeys.map((k) => <tr key={k.keyId}><td>{k.name} <span className="ad-faint"><code>{k.prefix}…</code></span></td><td>{k.tenant}</td><td>{num(k.requests)}</td><td>{num(k.errors)}</td><td>{k.distinctIps}</td><td className="ad-mono">{k.lastIp || '—'}</td></tr>)}
          </tbody></table> : <Empty>No API traffic recorded today (counters are saved once a minute).</Empty>}
        </div>
      </>}

      <div className="ad-head" style={{ marginTop: 22 }}><div><h2 style={{ fontSize: 16 }}>Events</h2><p>Everything the detection rules flagged. Resolve an event once it is dealt with.</p></div>
        <span className="ad-row">
          <select className="ad-in" value={sev} onChange={(e) => { setSev(e.target.value); setPage(1); }} aria-label="Severity"><option value="">All severities</option><option value="critical">Critical</option><option value="warn">Warning</option><option value="info">Info</option></select>
          <select className="ad-in" value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }} aria-label="Type"><option value="">All types</option>{(ev.data?.kinds || []).map((k) => <option key={k} value={k}>{KIND[k] || k}</option>)}</select>
          <select className="ad-in" value={res} onChange={(e) => { setRes(e.target.value); setPage(1); }} aria-label="State"><option value="false">Open</option><option value="true">Resolved</option><option value="">All</option></select>
        </span></div>
      <div className="ad-tw"><table className="ad-t"><thead><tr><th>When</th><th>Severity</th><th>Type</th><th>What happened</th><th>Customer</th><th></th></tr></thead><tbody>
        {ev.data?.rows.map((e) => <tr key={e.id}><td><When iso={e.at} /></td><td><Badge v={e.severity} tone={tone(e.severity)} /></td><td>{KIND[e.kind] || e.kind}</td><td className="wrap">{e.detail}</td>
          <td>{e.tenantId ? <a href="#customers" onClick={(x) => { x.preventDefault(); openCustomer(e.tenantId!); }}>{e.tenant}</a> : <span className="ad-faint">platform</span>}</td>
          <td><span className="ad-row">
            {e.keyId && !e.keySuspended && <button className="ad-btn sm bad" onClick={() => setAsk({ what: 'suspend', id: e.keyId!, label: `${e.keyName || 'key'} (${e.tenant || ''})` })}>Suspend key</button>}
            {e.tenantId && e.severity === 'critical' && <button className="ad-btn sm bad" onClick={() => setAsk({ what: 'lock', id: e.tenantId!, label: e.tenant || 'this workspace' })}>Lock workspace</button>}
            {!e.resolved && <button className="ad-btn sm" onClick={() => act(`/api/admin/security/events/${e.id}/resolve`)}>Resolve</button>}
          </span></td></tr>)}
      </tbody></table>{ev.loading && !ev.data && <Loading />}{ev.data && !ev.data.rows.length && <Empty>No events match.</Empty>}</div>
      {ev.data && <Pager page={ev.data.page} pages={ev.data.pages} total={ev.data.total} onPage={setPage} />}

      {ask && <Confirm title={ask.what === 'lock' ? `Lock ${ask.label}?` : `Suspend ${ask.label}?`} danger confirmLabel={ask.what === 'lock' ? 'Lock workspace' : 'Suspend key'} onCancel={() => setAsk(null)}
        onConfirm={async () => { await act(ask.what === 'lock' ? `/api/admin/security/tenants/${ask.id}/lock` : `/api/admin/security/keys/${ask.id}/suspend`, ask.what === 'suspend' ? { reason: 'Suspended by the platform team' } : undefined); setAsk(null); }}>
        {ask.what === 'lock' ? 'The workspace and all its API keys stop working at once. Re-activate it from Customers when it is safe.' : 'The key stops working immediately. The owner can see why in API & usage; only you can restore a key you suspended.'}</Confirm>}
    </div>
  );
}
