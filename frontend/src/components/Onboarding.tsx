import { KeyboardEvent, useEffect, useState } from 'react';
import { api } from '../api';
import { Logo } from './Logo';
import '../onboarding.css';

type PlanId = 'trial' | 'starter' | 'growth';
type Plan = { id: PlanId; name: string; priceUsd: number; pricePkr: number; maxInstances: number; maxMsgsDay: number; maxMembers: number; aiCallsDay: number };
type Info = {
  needed: boolean; stripe: boolean; trialDays: number; plans: Plan[];
  prefill: { name: string; business: string; website: string; country: string; about: string; phone: string; email: string };
};
type Result = { ok: boolean; checkoutUrl?: string; message?: string };

const COUNTRIES: Array<[string, string]> = [
  ['PK', 'Pakistan'], ['IN', 'India'], ['BD', 'Bangladesh'], ['AE', 'United Arab Emirates'], ['SA', 'Saudi Arabia'],
  ['GB', 'United Kingdom'], ['US', 'United States'], ['CA', 'Canada'], ['AU', 'Australia'], ['NG', 'Nigeria'],
  ['KE', 'Kenya'], ['ZA', 'South Africa'], ['EG', 'Egypt'], ['TR', 'Turkey'], ['ID', 'Indonesia'], ['MY', 'Malaysia'],
  ['BR', 'Brazil'], ['MX', 'Mexico'], ['DE', 'Germany'], ['FR', 'France'],
];
const STEPS = ['Welcome', 'Plan', 'Your business', 'WhatsApp'];
const FALLBACK_PLANS: Plan[] = [
  { id: 'trial', name: 'Free trial', priceUsd: 0, pricePkr: 0, maxInstances: 1, maxMsgsDay: 50, maxMembers: 1, aiCallsDay: 0 },
];

function readPlan(): PlanId {
  try { const p = localStorage.getItem('cf_plan'); if (p === 'starter' || p === 'growth') return p; } catch { /* storage blocked */ }
  return 'trial';
}
const num = (n: number) => n.toLocaleString('en-US');

export function Onboarding({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [info, setInfo] = useState<Info | null>(null);
  const [loadErr, setLoadErr] = useState('');
  const [plan, setPlan] = useState<PlanId>(readPlan);
  const [f, setF] = useState({ name: '', business: '', website: '', country: '', about: '', phone: '' });
  const [sendDetails, setSendDetails] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');

  useEffect(() => {
    api<Info>('/api/onboarding').then((i) => {
      setInfo(i);
      setF({ name: i.prefill.name, business: i.prefill.business, website: i.prefill.website, country: i.prefill.country, about: i.prefill.about, phone: i.prefill.phone });
    }).catch((e: any) => setLoadErr(e.message));
  }, []);

  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((p) => ({ ...p, [k]: e.target.value }));
  const plans = info?.plans?.length ? info.plans : FALLBACK_PLANS;
  const trialDays = info?.trialDays ?? 14;
  const last = STEPS.length - 1;
  const next = () => { setErr(''); setStep((s) => Math.min(last, s + 1)); };
  const back = () => { setErr(''); setStep((s) => Math.max(0, s - 1)); };

  const submit = async (body: Record<string, unknown>, openNumbers: boolean) => {
    setBusy(true); setErr('');
    try {
      const r = await api<Result>('/api/onboarding', 'POST', body);
      if (r.checkoutUrl) { location.href = r.checkoutUrl; return; }
      if (r.message) { setMsg(r.message); await new Promise((ok) => setTimeout(ok, 1800)); }
      if (openNumbers) location.hash = 'numbers';
      onDone();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };
  const skipAll = () => submit({ skip: true, plan_id: 'trial' }, false);
  const finish = (openNumbers: boolean) => {
    const details = sendDetails ? { name: f.name.trim(), business: f.business.trim(), website: f.website.trim(), country: f.country, about: f.about.trim(), phone: f.phone.trim() } : {};
    return submit({ plan_id: plan, ...details, skip: false }, openNumbers);
  };
  const nextStep = () => { if (step === 2) setSendDetails(true); next(); };

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Enter' || busy || e.shiftKey) return;
    const t = e.target as HTMLElement;
    if (t.tagName === 'TEXTAREA' || t.tagName === 'BUTTON' || t.tagName === 'A') return;
    e.preventDefault();
    if (step < last) nextStep(); else finish(true);
  };

  return (
    <div className="auth-page ob-page" onKeyDown={onKey}>
      <section className="auth-brand">
        <a className="auth-home" href={import.meta.env.VITE_SITE_URL || '/'}><Logo size={36} /></a>
        <div className="auth-pitch">
          <h2>Welcome aboard. <em>Let's set you up.</em></h2>
          <p>A minute of setup makes the AI sound like your business from the very first message.</p>
          <ul>
            <li><span>✓</span>No card needed for the free trial</li>
            <li><span>✓</span>Warm-up and daily caps keep your number safe</li>
            <li><span>✓</span>You approve every message before it is sent</li>
          </ul>
        </div>
        <small>© {new Date().getFullYear()} ConnectFlow</small>
      </section>

      <div className="auth-form-wrap ob-wrap">
        <div className="ob-main">
          <div className="ob-head">
            <span className="auth-mobile-logo"><Logo size={30} /></span>
            <div className="ob-progress" aria-label={`Step ${step + 1} of ${STEPS.length}`}>
              {STEPS.map((s, i) => <span key={s} className={`ob-dot${i === step ? ' on' : i < step ? ' done' : ''}`} title={s} />)}
              <small className="muted">Step {step + 1} of {STEPS.length}</small>
            </div>
          </div>

          {loadErr && <div className="err">{loadErr}</div>}

          {step === 0 && (
            <div className="ob-step">
              <h1>Welcome to ConnectFlow</h1>
              <p className="muted ob-sub">Here is how it works. Setup takes about a minute.</p>
              <div className="ob-cards">
                <div className="ob-card"><span className="ob-ico">1</span><div><b>Find leads</b><p>Import your own list, or let the AI scrape and search for people who want what you sell.</p></div></div>
                <div className="ob-card"><span className="ob-ico">2</span><div><b>AI writes the first message</b><p>You approve it, then it sends, with warm-up and daily caps to protect your number.</p></div></div>
                <div className="ob-card"><span className="ob-ico">3</span><div><b>Reply, post Statuses and grow</b><p>Answer everyone from one inbox and post WhatsApp Status updates to stay visible.</p></div></div>
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="ob-step">
              <h1>Choose a plan</h1>
              <p className="muted ob-sub">Start free, upgrade whenever you like. Paid plans open Stripe's secure card page at the end. Nothing is charged before that.</p>
              <div className="ob-plans" role="radiogroup" aria-label="Plan">
                {plans.map((p) => {
                  const free = p.id === 'trial';
                  return (
                    <button type="button" key={p.id} role="radio" aria-checked={plan === p.id} className={`ob-plan${plan === p.id ? ' on' : ''}`} onClick={() => setPlan(p.id)}>
                      <span className="ob-plan-name">{free ? 'Free' : p.name}{p.id === 'growth' && <em>Popular</em>}</span>
                      <span className="ob-price">{free ? <b>$0</b> : <><b>${p.priceUsd}</b><small>/month</small></>}</span>
                      <span className="ob-plan-note">{free ? `${trialDays}-day free trial, no card needed` : 'Billed monthly, cancel anytime'}</span>
                      <ul>
                        <li>{num(p.maxInstances)} WhatsApp {p.maxInstances === 1 ? 'number' : 'numbers'}</li>
                        <li>{num(p.maxMsgsDay)} messages per day</li>
                        <li>{num(p.maxMembers)} team {p.maxMembers === 1 ? 'member' : 'members'}</li>
                      </ul>
                    </button>
                  );
                })}
              </div>
              {plan !== 'trial' && info && !info.stripe && <p className="muted ob-note">Online payments are not enabled yet. We will pass your request to the team.</p>}
            </div>
          )}

          {step === 2 && (
            <div className="ob-step">
              <h1>About you and your business</h1>
              <p className="muted ob-sub">All optional. The AI uses this to write messages that sound like you.</p>
              <div className="ob-form">
                <div className="ob-two">
                  <label>Your name<input value={f.name} onChange={set('name')} autoComplete="name" autoFocus /></label>
                  <label>Business name<input value={f.business} onChange={set('business')} autoComplete="organization" /></label>
                </div>
                <div className="ob-two">
                  <label>Website<input value={f.website} onChange={set('website')} placeholder="https://" inputMode="url" autoComplete="url" /></label>
                  <label>Country
                    <select value={f.country} onChange={set('country')}>
                      <option value="">Other / skip</option>
                      {COUNTRIES.map(([c, n]) => <option key={c} value={c}>{n}</option>)}
                    </select>
                  </label>
                </div>
                <label>What do you sell? Services, prices, what makes you different (the AI uses this to write messages)
                  <textarea rows={4} maxLength={4000} value={f.about} onChange={set('about')} />
                </label>
                <label>Your WhatsApp number<input value={f.phone} onChange={set('phone')} placeholder="+92 300 1234567" inputMode="tel" autoComplete="tel" /></label>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="ob-step">
              <h1>Connect WhatsApp</h1>
              <p className="muted ob-sub">Open <b>WhatsApp numbers</b> and scan a QR code with your phone, the same way as WhatsApp Web. It takes about 30 seconds.</p>
              <div className="ob-qr" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="44" height="44" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M7 2h10a2 2 0 012 2v16a2 2 0 01-2 2H7a2 2 0 01-2-2V4a2 2 0 012-2zM9 8h2v2H9zM13 8h2v2h-2zM9 12h2v2H9zM14 13v1M13 16h2" /></svg>
                <span>Scan, and you are ready to send</span>
              </div>
              {plan !== 'trial' && <p className="muted ob-note">Next you will go to Stripe's secure card page to start the {plans.find((p) => p.id === plan)?.name ?? ''} plan.</p>}
              {msg && <div className="okbox">{msg}</div>}
            </div>
          )}

          {err && <div className="err">{err}</div>}

          <div className="ob-actions">
            {step > 0 && <button type="button" onClick={back} disabled={busy}>Back</button>}
            <span className="ob-gap" />
            {step === 2 && <button type="button" className="ghost" disabled={busy} onClick={() => { setSendDetails(false); next(); }}>Skip this step</button>}
            {step === 3 && <button type="button" disabled={busy} onClick={() => finish(false)}>Finish, I'll do it later</button>}
            {step < last && <button type="button" className="primary" disabled={busy} onClick={nextStep}>{step === 0 ? 'Get started' : 'Next'}</button>}
            {step === last && <button type="button" className="primary" disabled={busy} onClick={() => finish(true)}>{busy ? 'Saving…' : 'Finish and open WhatsApp numbers'}</button>}
          </div>
          <div className="ob-skip"><button type="button" className="link" disabled={busy} onClick={skipAll}>Skip setup</button></div>
        </div>
      </div>
    </div>
  );
}
