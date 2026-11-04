import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';

type Stats = { tenants: number; users: number; instances: number; instancesByStatus: Record<string, number> };
type T = { id: string; name: string; owner: string; plan: string; status: string; instances: number; createdAt: string };
type UpgradeReq = { id: string; tenant: string; currentPlan: string; requestedPlan: string; priceUsd: number; pricePkr: number; note: string; status: string; createdAt: string };
type I = { id: string; tenant: string; label: string; phone: string; status: string; engine: string; node: string };

export function Admin() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [tenants, setTenants] = useState<T[]>([]);
  const [insts, setInsts] = useState<I[]>([]);
  const [reqs, setReqs] = useState<UpgradeReq[]>([]);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try {
      const [s, t, i] = await Promise.all([api<Stats>('/api/admin/stats'), api<T[]>('/api/admin/tenants'), api<I[]>('/api/admin/instances')]);
      setStats(s); setTenants(t); setInsts(i); setReqs(await api<UpgradeReq[]>('/api/admin/upgrade-requests'));
    } catch (e: any) { setErr(e.message); }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);

  const resolve = async (id: string, what: 'approve' | 'reject') => { try { await api(`/api/admin/upgrade-requests/${id}/${what}`, 'POST'); load(); } catch (e: any) { setErr(e.message); } };
  const patch = async (id: string, body: object) => { try { await api(`/api/admin/tenants/${id}`, 'PATCH', body); load(); } catch (e: any) { setErr(e.message); } };

  return (
    <section>
      <h2>Admin</h2>
      {err && <div className="err">{err}</div>}
      {stats && (
        <div className="grid stats">
          <div className="card"><div className="big">{stats.tenants}</div><span className="muted">Customers</span></div>
          <div className="card"><div className="big">{stats.users}</div><span className="muted">Users</span></div>
          <div className="card"><div className="big">{stats.instances}</div><span className="muted">WhatsApp numbers</span></div>
          <div className="card"><div className="big">{stats.instancesByStatus.ready || 0}</div><span className="muted">Connected now</span></div>
        </div>
      )}
      <h3>Plan requests</h3>
      <div className="card table-wrap"><table>
        <thead><tr><th>Customer</th><th>From → To</th><th>Price</th><th>Note</th><th>Status</th><th></th></tr></thead>
        <tbody>
          {reqs.map((r) => (
            <tr key={r.id}><td>{r.tenant}</td><td>{r.currentPlan} → <b>{r.requestedPlan}</b></td><td>${r.priceUsd} / PKR {r.pricePkr.toLocaleString()}</td><td className="muted">{r.note || '—'}</td>
              <td><span className={`badge ${r.status === 'approved' ? 'ok' : r.status === 'rejected' ? 'bad' : 'warn'}`}>{r.status}</span></td>
              <td>{r.status === 'pending' && <span className="row"><button className="primary" onClick={() => resolve(r.id, 'approve')}>Payment received · Approve</button><button onClick={() => resolve(r.id, 'reject')}>Reject</button></span>}</td></tr>
          ))}
          {!reqs.length && <tr><td colSpan={6} className="muted">No requests.</td></tr>}
        </tbody></table></div>
      <h3>Customers</h3>
      <div className="card table-wrap"><table>
        <thead><tr><th>Company</th><th>Owner</th><th>Plan</th><th>Numbers</th><th>Status</th><th></th></tr></thead>
        <tbody>{tenants.map((t) => (
          <tr key={t.id}>
            <td>{t.name}</td><td>{t.owner}</td>
            <td><select value={t.plan} onChange={(e) => patch(t.id, { plan_id: e.target.value })}><option>trial</option><option>starter</option><option>growth</option></select></td>
            <td>{t.instances}</td>
            <td><span className={`badge ${t.status === 'active' ? 'ok' : 'bad'}`}>{t.status}</span></td>
            <td><button className="ghost" onClick={() => patch(t.id, { status: t.status === 'active' ? 'suspended' : 'active' })}>{t.status === 'active' ? 'Suspend' : 'Activate'}</button></td>
          </tr>))}
        </tbody></table></div>
      <h3>All WhatsApp numbers</h3>
      <div className="card table-wrap"><table>
        <thead><tr><th>Customer</th><th>Label</th><th>Phone</th><th>Status</th><th>Engine</th><th>Node</th></tr></thead>
        <tbody>{insts.map((i) => (<tr key={i.id}><td>{i.tenant}</td><td>{i.label}</td><td>{i.phone ? `+${i.phone.replace(/\D/g, '')}` : '—'}</td><td>{i.status}</td><td>{i.engine}</td><td>{i.node}</td></tr>))}</tbody>
      </table></div>
    </section>
  );
}
