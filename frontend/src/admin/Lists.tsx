import { useEffect, useState } from 'react';
import { Badge, Copy, Empty, ErrorBox, Loading, Pager, When, useDebounced, useGet } from './ui';

type Paged<T> = { rows: T[]; total: number; page: number; pages: number };

type U = { id: string; email: string; name: string; tenant: string | null; tenantId: string | null; role: string; verified: boolean; provider: string; isPlatformAdmin: boolean; createdAt: string; lastLoginAt: string | null; lastSeenAt: string | null };
export function Users({ openCustomer }: { openCustomer: (id: string) => void }) {
  const [q, setQ] = useState(''); const [page, setPage] = useState(1); const dq = useDebounced(q);
  useEffect(() => setPage(1), [dq]);
  const { data, err, loading } = useGet<Paged<U>>(`/api/admin/users?${new URLSearchParams({ q: dq, page: String(page) })}`);
  return (
    <div>
      <div className="ad-head"><div><h2>Users</h2><p>Everyone with an account, across all workspaces.</p></div></div>
      <input className="ad-in" style={{ width: '100%', maxWidth: 420, marginBottom: 12 }} placeholder="Search email or name…" value={q} onChange={(e) => setQ(e.target.value)} />
      <ErrorBox msg={err} />
      <div className="ad-tw"><table className="ad-t"><thead><tr><th>Email</th><th>Name</th><th>Workspace</th><th>Role</th><th>Sign-in</th><th>Confirmed</th><th>Last login</th><th>Last seen</th><th>Joined</th></tr></thead><tbody>
        {data?.rows.map((u) => <tr key={u.id}><td><Copy text={u.email} /> {u.isPlatformAdmin && <span className="ad-pill">ADMIN</span>}</td><td>{u.name || '—'}</td>
          <td>{u.tenantId ? <a href="#customers" onClick={(e) => { e.preventDefault(); openCustomer(u.tenantId!); }}>{u.tenant}</a> : <span className="ad-faint">none</span>}</td>
          <td><Badge v={u.role || '—'} tone="info" /></td><td>{u.provider}</td><td>{u.verified ? <Badge v="yes" tone="ok" /> : <Badge v="no" tone="warn" />}</td><td><When iso={u.lastLoginAt} /></td><td><When iso={u.lastSeenAt} /></td><td><When iso={u.createdAt} /></td></tr>)}
      </tbody></table>{loading && !data && <Loading />}{data && !data.rows.length && <Empty>No users match.</Empty>}</div>
      {data && <Pager page={data.page} pages={data.pages} total={data.total} onPage={setPage} />}
    </div>
  );
}

type A = { id: number; at: string; tenant: string | null; tenantId: string | null; userEmail: string | null; action: string; detail: string };
export function Activity({ openCustomer }: { openCustomer: (id: string) => void }) {
  const [q, setQ] = useState(''); const [action, setAction] = useState(''); const [page, setPage] = useState(1); const dq = useDebounced(q);
  useEffect(() => setPage(1), [dq, action]);
  const { data, err, loading, reload } = useGet<Paged<A> & { actions: string[] }>(`/api/admin/activity?${new URLSearchParams({ q: dq, action, page: String(page) })}`);
  return (
    <div>
      <div className="ad-head"><div><h2>Activity</h2><p>Platform-wide audit trail: sign-ups, logins, number changes, plan changes, key and webhook changes.</p></div><button className="ad-btn" onClick={reload}>Refresh</button></div>
      <div className="ad-row" style={{ marginBottom: 12 }}>
        <input className="ad-in" style={{ flex: 1, minWidth: 220 }} placeholder="Search email, workspace, detail…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="ad-in" value={action} onChange={(e) => setAction(e.target.value)}><option value="">All actions</option>{data?.actions.map((a) => <option key={a} value={a}>{a}</option>)}</select>
      </div>
      <ErrorBox msg={err} />
      <div className="ad-tw"><table className="ad-t"><thead><tr><th>When</th><th>Workspace</th><th>Who</th><th>Action</th><th>Detail</th></tr></thead><tbody>
        {data?.rows.map((a) => <tr key={a.id}><td><When iso={a.at} /></td><td>{a.tenantId ? <a href="#customers" onClick={(e) => { e.preventDefault(); openCustomer(a.tenantId!); }}>{a.tenant}</a> : <span className="ad-faint">—</span>}</td><td>{a.userEmail || <span className="ad-faint">system</span>}</td><td><code>{a.action}</code></td><td className="wrap">{a.detail}</td></tr>)}
      </tbody></table>{loading && !data && <Loading />}{data && !data.rows.length && <Empty>No events.</Empty>}</div>
      {data && <Pager page={data.page} pages={data.pages} total={data.total} onPage={setPage} />}
    </div>
  );
}

type I = { id: string; tenant: string; label: string; phone: string; status: string; engine: string; node: string; createdAt: string };
export function Numbers() {
  const { data, err, loading, reload } = useGet<I[]>('/api/admin/instances');
  const [f, setF] = useState('');
  const rows = (data || []).filter((i) => !f || i.status === f);
  const statuses = [...new Set((data || []).map((i) => i.status))];
  return (
    <div>
      <div className="ad-head"><div><h2>WhatsApp numbers</h2><p>All linked numbers across customers.</p></div><div className="ad-row"><select className="ad-in" value={f} onChange={(e) => setF(e.target.value)}><option value="">All statuses</option>{statuses.map((s) => <option key={s}>{s}</option>)}</select><button className="ad-btn" onClick={reload}>Refresh</button></div></div>
      <ErrorBox msg={err} />
      <div className="ad-tw"><table className="ad-t"><thead><tr><th>Customer</th><th>Label</th><th>Phone</th><th>Status</th><th>Engine</th><th>Node</th><th>Added</th><th>Id</th></tr></thead><tbody>
        {rows.map((i) => <tr key={i.id}><td>{i.tenant}</td><td>{i.label}</td><td>{i.phone ? `+${i.phone}` : '—'}</td><td><Badge v={i.status} /></td><td>{i.engine}</td><td>{i.node}</td><td><When iso={i.createdAt} /></td><td><Copy text={i.id} /></td></tr>)}
      </tbody></table>{loading && !data && <Loading />}{data && !rows.length && <Empty>No numbers.</Empty>}</div>
    </div>
  );
}
