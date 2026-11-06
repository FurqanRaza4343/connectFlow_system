import { FormEvent, useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { Overview } from './Overview';
import { CustomerDetail, Customers } from './Customers';
import { Activity, Numbers, Users } from './Lists';
import { Plans, Requests, System } from './Ops';
import { Appearance } from './Appearance';
import { Security } from './Security';
import { ADMIN_CLS, MfaEnrol, MfaStep } from '../components/Mfa';

type Me = { user: { id: string; email: string; name: string; isPlatformAdmin: boolean } };
const NAV: { id: string; label: string; icon: string }[] = [
  { id: 'overview', label: 'Overview', icon: 'M3 12l9-8 9 8M5 10v10h14V10' },
  { id: 'customers', label: 'Customers', icon: 'M3 21V8l9-5 9 5v13M9 21v-6h6v6' },
  { id: 'users', label: 'Users', icon: 'M9 11a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6M17 11a3 3 0 100-6M18.5 14.4c2 .6 3.5 2.4 3.5 5.6' },
  { id: 'activity', label: 'Activity', icon: 'M3 12h4l3-8 4 16 3-8h4' },
  { id: 'numbers', label: 'Numbers', icon: 'M7 2h10a2 2 0 012 2v16a2 2 0 01-2 2H7a2 2 0 01-2-2V4a2 2 0 012-2z' },
  { id: 'security', label: 'Security', icon: 'M12 3l8 3v6c0 4.500-3.200 8-8 9-4.800-1-8-4.500-8-9V6zM9 12l2 2 4-4' },
  { id: 'requests', label: 'Plan requests', icon: 'M3 7h18v10H3zM3 11h18' },
  { id: 'plans', label: 'Plans', icon: 'M4 6h16M4 12h16M4 18h10' },
  { id: 'appearance', label: 'Appearance', icon: 'M12 3a9 9 0 100 18c1.1 0 2-.9 2-2 0-.5-.2-1-.5-1.300-.3-.4-.5-.8-.5-1.300 0-1.100.9-2 2-2H17a4 4 0 004-4c0-4.400-4-8-9-8zM7.500 11a1 1 0 100-2 1 1 0 000 2zM11 7.500a1 1 0 100-2 1 1 0 000 2zM15.500 9a1 1 0 100-2 1 1 0 000 2z' },
  { id: 'system', label: 'System', icon: 'M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01' },
];
const Brand = () => <span className="ad-brand"><img src="/logo.svg" alt="" />ConnectFlow <span className="ad-pill">ADMIN</span></span>;

function Login({ onDone, notice }: { onDone: () => void; notice?: string }) {
  const [email, setEmail] = useState(''); const [pw, setPw] = useState(''); const [err, setErr] = useState(notice || ''); const [busy, setBusy] = useState(false);
  const [mfaToken, setMfaToken] = useState(''); const [enrol, setEnrol] = useState(false);
  useEffect(() => { api<{ enrolRequired: boolean }>('/api/auth/mfa/status').then((m) => { if (m.enrolRequired) setEnrol(true); }).catch(() => {}); }, []);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr('');
    try {
      const r = await api<any>('/api/auth/login', 'POST', { email, password: pw });
      if (r.mfaRequired) setMfaToken(r.mfaToken); else if (r.enrolRequired) setEnrol(true); else onDone();
    } catch (x: any) { setErr(x.message); } finally { setBusy(false); }
  }
  if (mfaToken) return <div className="ad-login"><div className="ad-box"><Brand /><MfaStep token={mfaToken} cls={ADMIN_CLS} onDone={() => onDone()} onCancel={() => setMfaToken('')} /></div></div>;
  if (enrol) return <div className="ad-login"><div className="ad-box"><Brand /><MfaEnrol forced cls={ADMIN_CLS} onDone={() => onDone()} /></div></div>;
  return (
    <div className="ad-login"><form onSubmit={submit}>
      <Brand /><h1>Sign in to the admin console</h1>
      <p className="ad-muted" style={{ margin: 0 }}>Platform administrators only.</p>
      <label>Email<input className="ad-in" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" autoFocus /></label>
      <label>Password<input className="ad-in" type="password" value={pw} onChange={(e) => setPw(e.target.value)} required autoComplete="current-password" /></label>
      {err && <div className="ad-err" role="alert">{err}</div>}
      <button className="ad-btn pri" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
    </form></div>
  );
}

function parse(): { page: string; id?: string } {
  const [page, id] = (location.hash.slice(1) || 'overview').split('/');
  return { page, id };
}

export function AdminApp() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [route, setRoute] = useState(parse());
  const [menu, setMenu] = useState(false);
  const [pending, setPending] = useState(0);

  const load = useCallback(() => api<Me>('/api/auth/me').then(setMe).catch(() => setMe(null)), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { const h = () => { setRoute(parse()); setMenu(false); window.scrollTo(0, 0); }; addEventListener('hashchange', h); return () => removeEventListener('hashchange', h); }, []);
  const refreshPending = useCallback(() => { api<any[]>('/api/admin/upgrade-requests').then((r) => setPending(r.filter((x) => x.status === 'pending').length)).catch(() => {}); }, []);
  useEffect(() => { if (me?.user.isPlatformAdmin) refreshPending(); }, [me, refreshPending]);

  const go = (p: string, id?: string) => { location.hash = id ? `${p}/${id}` : p; };
  const logout = async () => { await api('/api/auth/logout', 'POST').catch(() => {}); setMe(null); };

  if (me === undefined) return <div className="ad-root"><div className="ad-load" style={{ paddingTop: 80 }}>Loading…</div></div>;
  if (me === null) return <div className="ad-root"><Login onDone={load} /></div>;
  if (!me.user.isPlatformAdmin) return (
    <div className="ad-root"><div className="ad-login"><div className="ad-box"><Brand /><h1>Not allowed</h1><p className="ad-muted" style={{ margin: 0 }}>This account is not a platform admin.</p><div className="ad-row"><button className="ad-btn pri" onClick={logout}>Sign out</button></div></div></div></div>
  );

  const { page, id } = route;
  const view = () => {
    if (page === 'customers' && id) return <CustomerDetail id={id} back={() => go('customers')} />;
    switch (page) {
      case 'customers': return <Customers open={(i) => go('customers', i)} />;
      case 'users': return <Users openCustomer={(i) => go('customers', i)} />;
      case 'activity': return <Activity openCustomer={(i) => go('customers', i)} />;
      case 'numbers': return <Numbers />;
      case 'requests': return <Requests openCustomer={(i) => go('customers', i)} onChange={refreshPending} />;
      case 'security': return <Security openCustomer={(i) => go('customers', i)} />;
      case 'plans': return <Plans />;
      case 'system': return <System />;
      case 'appearance': return <Appearance />;
      default: return <Overview go={(p) => go(p)} />;
    }
  };

  return (
    <div className="ad-root ad-shell">
      <aside className={`ad-side${menu ? ' open' : ''}`}>
        <Brand />
        <nav className="ad-nav">
          {NAV.map((n) => (
            <button key={n.id} className={page === n.id ? 'on' : ''} onClick={() => go(n.id)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={n.icon} /></svg>{n.label}
              {n.id === 'requests' && pending > 0 && <span className="n">{pending}</span>}
            </button>
          ))}
        </nav>
        <div className="ad-me"><b title={me.user.email}>{me.user.email}</b><button className="ad-btn sm" onClick={logout}>Sign out</button></div>
      </aside>
      {menu && <div className="ad-scrim" onClick={() => setMenu(false)} />}
      <div style={{ minWidth: 0 }}>
        <div className="ad-top"><button className="ad-btn sm" aria-label="Menu" onClick={() => setMenu(true)}>☰</button><Brand /></div>
        <main className="ad-main">{view()}</main>
      </div>
    </div>
  );
}
