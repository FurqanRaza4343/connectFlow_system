import { useCallback, useEffect, useState } from 'react';
import { api, Me } from '../api';

type Member = { id: string; userId: string; email: string; name: string; role: string; verified: boolean; isYou: boolean };
type Invite = { id: string; email: string; role: string; expiresAt: string };

export function Team({ me }: { me: Me }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [limit, setLimit] = useState(1);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('operator');
  const [err, setErr] = useState('');
  const [ok, setOk] = useState('');
  const owner = me.role === 'owner';

  const load = useCallback(async () => {
    try { const r = await api<{ members: Member[]; invites: Invite[]; limit: number }>('/api/team'); setMembers(r.members); setInvites(r.invites); setLimit(r.limit); }
    catch (e: any) { setErr(e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const act = async (fn: () => Promise<unknown>, msg = '') => { setErr(''); setOk(''); try { await fn(); setOk(msg); load(); } catch (e: any) { setErr(e.message); } };

  return (
    <section>
      <h2>Team</h2>
      <p className="muted">{members.length + invites.length} of {limit} seat(s) used on your {me.tenant!.plan.name} plan.</p>
      {err && <div className="err">{err}</div>}{ok && <div className="okbox">{ok}</div>}

      <div className="card stack">
        <b>Invite a teammate</b>
        <div className="row">
          <input style={{ flex: 2, minWidth: 220 }} type="email" placeholder="teammate@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role">
            {owner && <option value="admin">Admin — manage everything except billing</option>}
            <option value="operator">Operator — send & manage numbers</option>
            <option value="viewer">Viewer — read only</option>
          </select>
          <button className="primary" disabled={!email} onClick={() => act(async () => { await api('/api/team/invites', 'POST', { email, role }); setEmail(''); }, 'Invitation sent.')}>Send invite</button>
        </div>
      </div>

      <h3 style={{ marginTop: 24 }}>Members</h3>
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Person</th><th>Role</th><th>Email</th><th></th></tr></thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <td>{m.name || '—'} {m.isYou && <span className="badge ok">you</span>}</td>
                <td>{owner && m.role !== 'owner' && !m.isYou
                  ? <select value={m.role} onChange={(e) => act(() => api(`/api/team/members/${m.id}`, 'PATCH', { role: e.target.value }))}><option value="admin">admin</option><option value="operator">operator</option><option value="viewer">viewer</option></select>
                  : m.role}</td>
                <td className="muted">{m.email} {!m.verified && <span className="badge warn">unconfirmed</span>}</td>
                <td>{m.role !== 'owner' && !m.isYou && <button className="danger" onClick={() => confirm(`Remove ${m.email}?`) && act(() => api(`/api/team/members/${m.id}`, 'DELETE'))}>Remove</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {invites.length > 0 && <>
        <h3 style={{ marginTop: 24 }}>Pending invitations</h3>
        <div className="card table-wrap"><table><tbody>
          {invites.map((i) => (
            <tr key={i.id}><td>{i.email}</td><td>{i.role}</td><td className="muted">expires {new Date(i.expiresAt).toLocaleDateString()}</td><td><button onClick={() => act(() => api(`/api/team/invites/${i.id}`, 'DELETE'))}>Cancel</button></td></tr>
          ))}
        </tbody></table></div>
      </>}
    </section>
  );
}
