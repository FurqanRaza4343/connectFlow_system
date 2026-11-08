import { useEffect, useState } from 'react';
import { api } from '../api';
import { BarChart, Badge, Confirm, Copy, Empty, ErrorBox, HBars, Kpi, Loading, Pager, When, abs, num, useDebounced, useGet } from './ui';

type Row = { id: string; name: string; ownerEmail: string; ownerName: string; plan: string; status: string; createdAt: string; lastActiveAt: string | null; numbers: number; members: number; leads: number; messagesToday: number; messagesTotal: number };
type List = { rows: Row[]; total: number; page: number; pages: number };
type Plan = { id: string; name: string };

export function Customers({ open }: { open: (id: string) => void }) {
  const [q, setQ] = useState(''); const [status, setStatus] = useState(''); const [plan, setPlan] = useState(''); const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  useEffect(() => setPage(1), [dq, status, plan]);
  const { data, err, loading } = useGet<List>(`/api/admin/customers?${new URLSearchParams({ q: dq, status, plan, page: String(page) })}`);
  const plans = useGet<Plan[]>('/api/admin/plans').data || [];
  return (
    <div>
      <div className="ad-head"><div><h2>Customers</h2><p>Every workspace on the platform. Message content is never shown, only counts.</p></div></div>
      <div className="ad-row" style={{ marginBottom: 12 }}>
        <input className="ad-in" style={{ flex: 1, minWidth: 220 }} placeholder="Search company, owner email…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="ad-in" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any status</option><option value="active">active</option><option value="suspended">suspended</option></select>
        <select className="ad-in" value={plan} onChange={(e) => setPlan(e.target.value)}><option value="">Any plan</option>{plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
      </div>
      <ErrorBox msg={err} />
      <div className="ad-tw"><table className="ad-t">
        <thead><tr><th>Company</th><th>Owner</th><th>Plan</th><th>Status</th><th className="r">Numbers</th><th className="r">Team</th><th className="r">Leads</th><th className="r">Msgs today</th><th className="r">Msgs total</th><th>Last active</th><th>Created</th></tr></thead>
        <tbody>
          {data?.rows.map((r) => (
            <tr key={r.id} className="ad-click" onClick={() => open(r.id)}>
              <td><b>{r.name}</b></td>
              <td>{r.ownerName && <span>{r.ownerName} · </span>}<Copy text={r.ownerEmail} /></td>
              <td><Badge v={r.plan} tone="info" /></td><td><Badge v={r.status} /></td>
              <td className="r">{num(r.numbers)}</td><td className="r">{num(r.members)}</td><td className="r">{num(r.leads)}</td><td className="r">{num(r.messagesToday)}</td><td className="r">{num(r.messagesTotal)}</td>
              <td><When iso={r.lastActiveAt} /></td><td><When iso={r.createdAt} /></td>
            </tr>
          ))}
        </tbody>
      </table>
        {loading && !data && <Loading />}{data && !data.rows.length && <Empty>No customers match.</Empty>}
      </div>
      {data && <Pager page={data.page} pages={data.pages} total={data.total} onPage={setPage} />}
    </div>
  );
}

type Detail = {
  tenant: { id: string; name: string; plan: string; status: string; createdAt: string; website: string; brandColor: string | null };
  members: { userId: string; email: string; name: string; role: string; verified: boolean; lastLoginAt: string | null; lastSeenAt: string | null; provider: string }[];
  instances: { id: string; label: string; phone: string; status: string; createdAt: string; outreachEnabled: boolean; sentToday: number; sent7d: number; replies7d: number }[];
  keys: { id: string; name: string; prefix: string; lastUsedAt: string | null; expiresAt: string | null; revoked: boolean; status: string; requestsToday: number; requests7d: number; errors7d: number; distinctIps7d: number; lastIp: string; lastUserAgent: string; lastSeenAt: string | null; suspendedReason: string; suspendedBy: string; allowedIps: string }[];
  webhooks: { id: string; url: string; active: boolean; events: string[]; deliveries: { success: number; failed: number; pending: number } }[];
  leads: { total: number; byStatus: Record<string, number> };
  usage: { today: number; limit: number; days: { day: string; count: number }[]; aiToday: number; aiLimit: number; aiTokensToday?: number; aiCostTodayUsd?: number; aiCostMonthUsd?: number; aiRetriesToday?: number };
  requests: { id: string; planId: string; status: string; createdAt: string; note: string }[];
  activity: { at: string; action: string; detail: string; userEmail: string | null }[];
};
const TABS = ['Overview', 'People', 'Numbers', 'API & webhooks', 'Leads', 'Activity'] as const;

export function CustomerDetail({ id, back }: { id: string; back: () => void }) {
  const { data: d, err, loading, reload, setErr } = useGet<Detail>(`/api/admin/customers/${id}`);
  const plans = useGet<{ id: string; name: string }[]>('/api/admin/plans').data || [];
  const [tab, setTab] = useState<(typeof TABS)[number]>('Overview');
  const [dlg, setDlg] = useState<'delete' | 'plan' | 'suspend' | null>(null);
  const [newPlan, setNewPlan] = useState('');
  if (loading && !d) return <Loading />;
  if (!d) return <div><button className="ad-btn" onClick={back}>← Customers</button><ErrorBox msg={err || 'Not found'} /></div>;
  const t = d.tenant;
  const patch = async (body: object) => { try { await api(`/api/admin/tenants/${t.id}`, 'PATCH', body); setDlg(null); reload(); } catch (e: any) { setErr(e.message); setDlg(null); } };
  const owner = d.members.find((m) => m.role === 'owner');

  return (
    <div>
      <button className="ad-btn sm" onClick={back}>← Customers</button>
      <div className="ad-head" style={{ marginTop: 12 }}>
        <div><h2>{t.name} <Badge v={t.status} /> <Badge v={t.plan} tone="info" /></h2><p>{owner && <><Copy text={owner.email} /> · </>}created <When iso={t.createdAt} />{t.website && <> · <a href={t.website} target="_blank" rel="noreferrer noopener">{t.website.replace(/^https?:\/\//, '')}</a></>}</p></div>
        <div className="ad-row">
          <button className="ad-btn" onClick={() => { setNewPlan(t.plan); setDlg('plan'); }}>Change plan</button>
          {t.status === 'active' ? <button className="ad-btn bad" onClick={() => setDlg('suspend')}>Suspend</button> : <button className="ad-btn pri" onClick={() => patch({ status: 'active' })}>Activate</button>}
          <button className="ad-btn bad" onClick={() => setDlg('delete')}>Delete</button>
        </div>
      </div>
      <ErrorBox msg={err} />
      <div className="ad-tabs" role="tablist">{TABS.map((x) => <button key={x} role="tab" className={tab === x ? 'on' : ''} onClick={() => setTab(x)}>{x}</button>)}</div>

      {tab === 'Overview' && (
        <div className="ad-grid">
          <div className="ad-grid ad-kpis"><Kpi v={num(d.usage.today)} l="Messages today" s={`limit ${num(d.usage.limit)}`} /><Kpi v={d.instances.length} l="Numbers" s={`${d.instances.filter((i) => i.status === 'ready').length} connected`} /><Kpi v={d.members.length} l="People" /><Kpi v={num(d.leads.total)} l="Leads" /><Kpi v={d.keys.filter((k) => !k.revoked).length} l="Active API keys" /><Kpi v={`${num(d.usage.aiToday)} / ${num(d.usage.aiLimit)}`} l="AI calls today" s="plan daily limit" /><Kpi v={num(d.usage.aiTokensToday)} l="AI tokens today" s={`${num(d.usage.aiRetriesToday)} provider retries`} /><Kpi v={(d.usage.aiCostMonthUsd ?? 0) > 0 ? `$${(d.usage.aiCostMonthUsd ?? 0).toFixed(2)}` : 'n/a'} l="AI cost this month (est.)" s={(d.usage.aiCostTodayUsd ?? 0) > 0 ? `today $${(d.usage.aiCostTodayUsd ?? 0).toFixed(4)}` : 'needs AI_PRICE_* settings'} /><Kpi v={d.webhooks.length} l="Webhooks" /></div>
          <div className="ad-grid ad-2">
            <div className="ad-card"><h3>Messages, last days</h3><BarChart data={d.usage.days} label="Messages per day" /></div>
            <div className="ad-card"><h3>Facts</h3><dl className="ad-kv"><dt>Tenant id</dt><dd><Copy text={t.id} /></dd><dt>Brand colour</dt><dd>{t.brandColor ? <><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 3, background: t.brandColor, marginRight: 6 }} />{t.brandColor}</> : 'default'}</dd><dt>Created</dt><dd>{abs(t.createdAt)}</dd><dt>Plan requests</dt><dd>{d.requests.length ? d.requests.map((r) => <div key={r.id}><Badge v={r.status} /> → {r.planId} <span className="ad-faint"><When iso={r.createdAt} />{r.note && ` · ${r.note}`}</span></div>) : 'none'}</dd></dl></div>
          </div>
          <div className="ad-note">Message content is never shown here. Admins see counts, statuses and audit events only.</div>
        </div>
      )}

      {tab === 'People' && <div className="ad-tw"><table className="ad-t"><thead><tr><th>Person</th><th>Email</th><th>Role</th><th>Sign-in</th><th>Email confirmed</th><th>Last login</th><th>Last seen</th><th></th></tr></thead><tbody>
        {d.members.map((m) => <tr key={m.userId}><td>{m.name || '—'}</td><td><Copy text={m.email} /></td><td><Badge v={m.role} tone="info" /></td><td>{m.provider}</td><td>{m.verified ? <Badge v="yes" tone="ok" /> : <Badge v="no" tone="warn" />}</td><td><When iso={m.lastLoginAt} /></td><td><When iso={m.lastSeenAt} /></td>
          <td>{!m.verified && <><button className="ad-btn sm" onClick={async () => { try { await api(`/api/admin/users/${m.userId}/verify-email`, 'POST'); reload(); } catch (e: any) { setErr(e.message); } }}>Mark confirmed</button> <button className="ad-btn sm" onClick={async () => { try { await api(`/api/admin/users/${m.userId}/resend-verification`, 'POST'); setErr(''); alert('Verification email sent.'); } catch (e: any) { setErr(e.message); } }}>Resend verification</button> </>}<button className="ad-btn sm" onClick={async () => { if (!confirm(`Email a password-reset link to ${m.email}?`)) return; try { await api(`/api/admin/users/${m.userId}/send-password-reset`, 'POST'); setErr(''); alert('Reset email sent.'); } catch (e: any) { setErr(e.message); } }}>Send reset email</button></td></tr>)}</tbody></table></div>}

      {tab === 'Numbers' && (d.instances.length ? <div className="ad-tw"><table className="ad-t"><thead><tr><th>Label</th><th>Phone</th><th>Status</th><th>Outreach</th><th className="r">Sent today</th><th className="r">Sent 7d</th><th className="r">Replies 7d</th><th>Added</th><th>Id</th></tr></thead><tbody>{d.instances.map((i) => <tr key={i.id}><td>{i.label}</td><td>{i.phone ? `+${i.phone}` : '—'}</td><td><Badge v={i.status} /></td><td>{i.outreachEnabled ? 'on' : 'API only'}</td><td className="r">{i.sentToday}</td><td className="r">{i.sent7d}</td><td className="r">{i.replies7d}</td><td><When iso={i.createdAt} /></td><td><Copy text={i.id} /></td></tr>)}</tbody></table></div> : <Empty>No numbers linked.</Empty>)}

      {tab === 'API & webhooks' && (
        <div className="ad-grid ad-2">
          <div className="ad-card" style={{ overflowX: 'auto' }}><h3>API keys</h3>{d.keys.length ? <table className="ad-t"><thead><tr><th>Name</th><th>Status</th><th>Today</th><th>7 days</th><th>IPs</th><th>Last IP</th><th>Last used</th><th>Expires</th><th></th></tr></thead><tbody>{d.keys.map((k) => <tr key={k.id}><td>{k.name}<div className="ad-faint"><code>{k.prefix}…</code>{k.allowedIps && ' · allow-list'}</div></td>
            <td><Badge v={k.status} tone={k.status === 'active' ? 'ok' : k.status === 'suspended' ? 'bad' : 'gray'} />{k.suspendedReason && <div className="ad-faint wrap" style={{ maxWidth: 180 }}>{k.suspendedBy === 'admin' ? 'by admin: ' : ''}{k.suspendedReason}</div>}</td>
            <td>{num(k.requestsToday)}</td><td>{num(k.requests7d)}{k.errors7d > 0 && <span className="ad-faint"> · {num(k.errors7d)} err</span>}</td><td>{k.distinctIps7d}</td><td className="ad-mono" title={k.lastUserAgent}>{k.lastIp || <span className="ad-faint">—</span>}</td><td><When iso={k.lastUsedAt} /></td><td>{k.expiresAt ? <When iso={k.expiresAt} /> : <span className="ad-faint">never</span>}</td>
            <td>{!k.revoked && (k.status === 'suspended'
              ? <button className="ad-btn sm" onClick={async () => { try { await api(`/api/admin/security/keys/${k.id}/unsuspend`, 'POST'); reload(); } catch (e: any) { setErr(e.message); } }}>Unsuspend</button>
              : k.status === 'active' && <button className="ad-btn sm bad" onClick={async () => { try { await api(`/api/admin/security/keys/${k.id}/suspend`, 'POST', { reason: 'Suspended by the platform team' }); reload(); } catch (e: any) { setErr(e.message); } }}>Suspend</button>)}</td></tr>)}</tbody></table> : <Empty>No keys.</Empty>}</div>
          <div className="ad-card"><h3>Webhooks</h3>{d.webhooks.length ? d.webhooks.map((w) => <div key={w.id} style={{ marginBottom: 10 }}><div className="ad-mono" style={{ overflowWrap: 'anywhere' }}>{w.url}</div><div className="ad-row"><Badge v={w.active ? 'active' : 'suspended'} tone={w.active ? 'ok' : 'gray'} /><span className="ad-muted">{w.events.join(', ')}</span></div><div className="ad-faint">delivered {num(w.deliveries.success)} · failed {num(w.deliveries.failed)} · pending {num(w.deliveries.pending)}</div></div>) : <Empty>No webhooks.</Empty>}</div>
        </div>
      )}

      {tab === 'Leads' && <div className="ad-card"><h3>Leads ({num(d.leads.total)})</h3><HBars rows={Object.entries(d.leads.byStatus).map(([label, value]) => ({ label, value }))} /><div className="ad-note">Only totals per status are visible to admins.</div></div>}

      {tab === 'Activity' && (d.activity.length ? <div className="ad-tw"><table className="ad-t"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Detail</th></tr></thead><tbody>{d.activity.map((a, i) => <tr key={i}><td><When iso={a.at} /></td><td>{a.userEmail || <span className="ad-faint">system</span>}</td><td><code>{a.action}</code></td><td className="wrap">{a.detail}</td></tr>)}</tbody></table></div> : <Empty>No activity recorded.</Empty>)}

      {dlg === 'plan' && <Confirm title="Change plan" confirmLabel="Save plan" onCancel={() => setDlg(null)} onConfirm={() => patch({ plan_id: newPlan })}>
        <select className="ad-in" style={{ width: '100%' }} value={newPlan} onChange={(e) => setNewPlan(e.target.value)}>{plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Confirm>}
      {dlg === 'suspend' && <Confirm title={`Suspend ${t.name}?`} danger confirmLabel="Suspend" onCancel={() => setDlg(null)} onConfirm={() => patch({ status: 'suspended' })}>Their people are locked out of the dashboard and API keys stop working until you activate the workspace again.</Confirm>}
      {dlg === 'delete' && <Confirm title={`Delete ${t.name}?`} danger confirmLabel="Delete permanently" requireText={t.name} onCancel={() => setDlg(null)}
        onConfirm={async () => { try { await api(`/api/admin/customers/${t.id}`, 'DELETE'); back(); } catch (e: any) { setErr(e.message); setDlg(null); } }}>This permanently deletes the workspace, its numbers, leads, keys and webhooks. This cannot be undone.</Confirm>}
    </div>
  );
}
