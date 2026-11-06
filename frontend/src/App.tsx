import { useCallback, useEffect, useState } from 'react';
import { api, Me } from './api';
import { applyTheme } from './theme';
import { Login } from './pages/Login';
import { Numbers } from './pages/Numbers';
import { ApiPage } from './pages/ApiPage';
import { Webhooks } from './pages/Webhooks';
import { Team } from './pages/Team';
import { Billing } from './pages/Billing';
import { Settings } from './pages/Settings';
import { Outreach, OUTREACH_TABS, OutreachTab } from './pages/Outreach';
import { Overview } from './pages/Overview';
import { Bell } from './components/Bell';
import { TokenFlow, tokenFromUrl } from './pages/TokenFlow';
import { Logo } from './components/Logo';
import { PlanPrompt, rememberPlanFromUrl } from './components/PlanPrompt';

rememberPlanFromUrl(); // pricing page links arrive as /?plan=starter#signup; Login strips the query, so capture it first

type Tab = 'overview' | 'numbers' | 'outreach' | 'api' | 'webhooks' | 'team' | 'billing' | 'settings';
const NAV: Array<{ id: Tab; label: string; icon: string; adminOnly?: boolean }> = [
  { id: 'overview', label: 'Overview', icon: 'M3 12l9-8 9 8M5 10v10h14V10' },
  { id: 'numbers', label: 'WhatsApp numbers', icon: 'M7 2h10a2 2 0 012 2v16a2 2 0 01-2 2H7a2 2 0 01-2-2V4a2 2 0 012-2zm5 17.5a1 1 0 100-2 1 1 0 000 2z' },
  { id: 'outreach', label: 'Outreach', icon: 'M4 5h16M4 12h16M4 19h10M18 17l2 2 3-4' },
  { id: 'api', label: 'API & usage', icon: 'M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14' },
  { id: 'webhooks', label: 'Webhooks', icon: 'M10 13a5 5 0 007.07 0l2.83-2.83a5 5 0 00-7.07-7.07l-1.4 1.4M14 11a5 5 0 00-7.07 0L4.1 13.83a5 5 0 007.07 7.07l1.4-1.4', adminOnly: true },
  { id: 'team', label: 'Team', icon: 'M9 11a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6M17 11a3 3 0 100-6M18.5 14.4c2 .6 3.5 2.4 3.5 5.6', adminOnly: true },
  { id: 'billing', label: 'Billing', icon: 'M3 7h18v10H3zM3 11h18M7 15h3' },
  { id: 'settings', label: 'Settings', icon: 'M12 15.5a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM19.4 15a1.7 1.7 0 00.3 1.9l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.9-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.9.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.9 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.9l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.9.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.9-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.9V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z', adminOnly: true },
];

export function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined); // undefined = loading
  const parse = () => { const [t, sub] = location.hash.slice(1).split('/'); return { tab: (NAV.some((n) => n.id === t) ? t : 'overview') as Tab, sub: (OUTREACH_TABS.includes(sub as OutreachTab) ? sub : 'leads') as OutreachTab }; };
  const [route, setRoute] = useState(parse);
  const tab = route.tab;
  const [menu, setMenu] = useState(false);
  const [flow] = useState(tokenFromUrl());
  const [notice, setNotice] = useState('');

  const load = useCallback(() => api<Me>('/api/auth/me').then(setMe).catch(() => setMe(null)), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { const h = () => setRoute(parse()); addEventListener('hashchange', h); return () => removeEventListener('hashchange', h); }, []);
  const b = me?.tenant?.brand;
  useEffect(() => { applyTheme({ accent: b?.color ?? null, sidebar: b?.sidebar ?? null, page: b?.page ?? null }); }, [b?.color, b?.sidebar, b?.page]);
  // go('outreach/approvals') from anywhere (overview cards, bell, tabs)
  const goHash = (h: string) => { setMenu(false); if (location.hash === `#${h}`) setRoute(parse()); else location.hash = h; };
  const go = (t: Tab) => goHash(t);

  if (flow && me !== undefined) return <TokenFlow flow={flow} me={me} onDone={(m) => { history.replaceState(null, '', location.pathname); if (m) setMe(m); location.reload(); }} />;
  if (me === undefined) return <div className="center muted">Loading…</div>;
  if (me === null) return <Login onDone={setMe} />;

  const t = me.tenant;
  const isAdmin = ['owner', 'admin'].includes(me.role);
  const items = NAV.filter((n) => (!n.adminOnly || isAdmin));
  const logout = async () => { await api('/api/auth/logout', 'POST'); setMe(null); };
  const resend = async () => { try { await api('/api/auth/resend-verification', 'POST'); setNotice('Confirmation email sent. Check your inbox.'); } catch (e: any) { setNotice(e.message); } };

  return (
    <div className="app">
      <aside className={`side${menu ? ' open' : ''}`}>
        <div className="side-top">
          {t?.brand.logo ? <span className="brandmark"><img src={t.brand.logo} alt="" /><b>{t.name}</b></span> : <Logo size={30} />}
        </div>
        <nav>
          {items.map((n) => (
            <button key={n.id} className={tab === n.id ? 'on' : ''} onClick={() => go(n.id)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={n.icon} /></svg>{n.label}
            </button>
          ))}
        </nav>
        <div className="side-bottom">
          <div className="who2"><b>{me.user.name || me.user.email}</b><small className="muted">{t?.name} · {t?.plan.name}</small></div>
          {t?.website && <a className="sidelink" href={t.website} target="_blank" rel="noopener noreferrer">Visit website ↗</a>}
          <button className="ghost" onClick={logout}>Log out</button>
          {t?.brand.logo && <small className="muted poweredby">Powered by ConnectFlow</small>}
        </div>
      </aside>
      {menu && <div className="scrim" onClick={() => setMenu(false)} />}

      <div className="content">
        <header className="topbar">
          <button className="menu-btn" aria-label="Menu" onClick={() => setMenu(true)}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M4 7h16M4 12h16M4 17h16" /></svg></button>
          <span className="muted topbar-title">{items.find((n) => n.id === tab)?.label}</span>
          <span className="topbar-gap" />
          <Bell onGo={goHash} />
        </header>
        {!me.user.emailVerified && (
          <div className="banner">
            <span>Confirm your email to add numbers and create API keys. We sent a link to <b>{me.user.email}</b>.</span>
            <button onClick={resend}>Resend email</button>
          </div>
        )}
        {notice && <div className="banner ok" onClick={() => setNotice('')}>{notice}</div>}
        {me.role === 'owner' && me.user.emailVerified && me.tenant?.plan.id === 'trial' && <PlanPrompt onGo={goHash} />}
        <main>
          {tab === 'overview' && t && <Overview me={me} go={goHash} />}
          {tab === 'numbers' && t && <Numbers me={me} />}
          {tab === 'outreach' && t && <Outreach me={me} sub={route.sub} onSub={(x) => goHash(`outreach/${x}`)} go={goHash} />}
          {tab === 'api' && t && <ApiPage me={me} />}
          {tab === 'webhooks' && t && isAdmin && <Webhooks />}
          {tab === 'team' && t && isAdmin && <Team me={me} />}
          {tab === 'billing' && t && <Billing me={me} />}
          {tab === 'settings' && t && isAdmin && <Settings me={me} onChange={load} />}
        </main>
      </div>
    </div>
  );
}
