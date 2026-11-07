import { useCallback, useEffect, useState } from 'react';
import { api, Me } from '../api';
import { AssetList, AssetUsage, fmtBytes } from '../components/media';

type Plan = { id: string; name: string; priceUsd: number; pricePkr: number; maxInstances: number; maxMsgsDay: number; maxMembers: number; aiCallsDay: number };
type Stripe = { enabled: boolean; status: string; renewsAt: string | null; cancelAtPeriodEnd: boolean; graceEndsAt: string | null; hasCustomer: boolean };
type B = {
  plan: Plan; usage: { messagesToday: number; aiCallsToday: number; numbers: number; members: number }; plans: Plan[];
  pending: { id: string; planId: string; createdAt: string } | null; history: { id: string; planId: string; status: string; createdAt: string }[];
  stripe: Stripe; overLimit: number;
};

const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '');

export function Billing({ me }: { me: Me }) {
  const [b, setB] = useState<B | null>(null);
  const [cur, setCur] = useState<'USD' | 'PKR'>('USD');
  const [note, setNote] = useState('');
  const [err, setErr] = useState('');
  const [ok, setOk] = useState('');
  const [busy, setBusy] = useState('');
  const [files, setFiles] = useState<AssetUsage | null>(null); // storage meters come from the Assets library (hidden if it is unavailable)
  const owner = me.role === 'owner';
  const load = useCallback(() => api<B>('/api/billing').then(setB).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { api<AssetList>('/api/assets').then((r) => setFiles(r.usage)).catch(() => setFiles(null)); }, []);

  // Coming back from Stripe Checkout: the plan changes when Stripe's webhook arrives (a few seconds), so poll briefly.
  useEffect(() => {
    const q = new URLSearchParams(location.search).get('checkout');
    if (!q) return;
    history.replaceState(null, '', location.pathname + location.hash);
    if (q === 'cancel') { setOk('Checkout was cancelled. You have not been charged.'); return; }
    setOk('Thank you! Confirming your payment with Stripe…');
    let n = 0;
    const t = setInterval(() => { n += 1; load(); if (n >= 10) clearInterval(t); }, 2500);
    return () => clearInterval(t);
  }, [load]);
  useEffect(() => {
    if (b && ok.startsWith('Thank you') && b.stripe.status && ['active', 'trialing'].includes(b.stripe.status)) setOk(`You are now on the ${b.plan.name} plan. Receipt and invoices are in Manage billing.`);
  }, [b, ok]);

  // USD is what Stripe charges. PKR is only an indicative local price (kept for customers who pay by bank transfer / invoice).
  const price = (p: Plan) => (p.priceUsd === 0 ? 'Free' : cur === 'USD' ? `$${p.priceUsd}` : `PKR ${p.pricePkr.toLocaleString()}`);

  async function subscribe(p: Plan) {
    setErr(''); setOk(''); setBusy(p.id);
    try {
      const r = await api<{ url?: string; changed?: boolean }>('/api/billing/checkout', 'POST', { plan_id: p.id });
      localStorage.removeItem('cf_plan');
      if (r.url) { location.href = r.url; return; }
      setOk(`Switching to ${p.name}. The change is applied as soon as Stripe confirms it.`); setTimeout(load, 2500);
    } catch (e: any) { setErr(e.message); } finally { setBusy(''); }
  }
  async function portal() {
    setErr(''); setBusy('portal');
    try { const r = await api<{ url: string }>('/api/billing/portal', 'POST'); location.href = r.url; } catch (e: any) { setErr(e.message); setBusy(''); }
  }
  async function requestUpgrade(p: Plan) {
    setErr(''); setOk('');
    try { await api('/api/billing/upgrade', 'POST', { plan_id: p.id, note }); setOk(`Request sent. We’ll contact you with payment details for the ${p.name} plan.`); setNote(''); load(); }
    catch (e: any) { setErr(e.message); }
  }
  if (!b) return <section>{err ? <div className="err">{err}</div> : <p className="muted">Loading…</p>}</section>;

  const s = b.stripe;
  const online = s.enabled;
  const subscribed = ['active', 'trialing', 'past_due'].includes(s.status);
  const rows: Array<[string, string, number, number]> = [
    ['Messages today', `${b.usage.messagesToday} / ${b.plan.maxMsgsDay}`, b.usage.messagesToday, b.plan.maxMsgsDay],
    ['AI drafts today', `${b.usage.aiCallsToday} / ${b.plan.aiCallsDay}`, b.usage.aiCallsToday, b.plan.aiCallsDay],
    ['WhatsApp numbers', `${b.usage.numbers} / ${b.plan.maxInstances}`, b.usage.numbers, b.plan.maxInstances],
    ['Team seats', `${b.usage.members} / ${b.plan.maxMembers}`, b.usage.members, b.plan.maxMembers],
  ];
  if (files && files.limitBytes > 0) {
    rows.push(['Media storage', `${fmtBytes(files.bytes)} / ${fmtBytes(files.limitBytes)}`, files.bytes, files.limitBytes]);
    if (files.limitCount > 0) rows.push(['Media files', `${files.count} / ${files.limitCount}`, files.count, files.limitCount]);
  }
  return (
    <section>
      <div className="row between"><div><h2>Billing</h2><p className="muted">You’re on the <b>{b.plan.name}</b> plan{s.status === 'active' && s.renewsAt && !s.cancelAtPeriodEnd ? <> · renews on {fmt(s.renewsAt)}</> : null}.</p></div>
        <div className="row">
          {online && owner && s.hasCustomer && <button onClick={portal} disabled={busy === 'portal'}>{busy === 'portal' ? 'Opening…' : 'Manage billing'}</button>}
          <div className="toggle2"><button className={cur === 'USD' ? 'on' : ''} onClick={() => setCur('USD')}>USD</button><button className={cur === 'PKR' ? 'on' : ''} onClick={() => setCur('PKR')}>PKR</button></div>
        </div></div>
      {err && <div className="err">{err}</div>}{ok && <div className="okbox">{ok}</div>}

      {s.status === 'past_due' && <div className="banner warn" style={{ borderRadius: 12, margin: '12px 0' }}>Your last payment failed. Update your card in <b>Manage billing</b>{s.graceEndsAt ? <> before {fmt(s.graceEndsAt)}</> : null}, or the workspace returns to the free Trial plan.</div>}
      {s.cancelAtPeriodEnd && s.status !== 'canceled' && <div className="banner" style={{ borderRadius: 12, margin: '12px 0' }}>Your subscription is cancelled and stays active until <b>{fmt(s.renewsAt)}</b>, then the workspace returns to the Trial plan. You can resume it in Manage billing.</div>}
      {b.overLimit > 0 && <div className="banner warn" style={{ borderRadius: 12, margin: '12px 0' }}>{b.overLimit} WhatsApp number{b.overLimit > 1 ? 's are' : ' is'} over your {b.plan.name} limit of {b.plan.maxInstances}. {b.overLimit > 1 ? 'They are' : 'It is'} kept, but paused: no messages are sent from {b.overLimit > 1 ? 'them' : 'it'}. Upgrade, or remove numbers you do not need.</div>}

      <div className="card usage">
        {rows.map(([label, text, v, max]) => (
          <div key={label}><div className="row between"><span>{label}</span><span className="muted">{text}</span></div>
            <div className="meterbar"><i style={{ width: `${Math.min(100, (v / Math.max(1, max)) * 100)}%` }} className={v / Math.max(1, max) > 0.85 ? 'hot' : ''} /></div></div>
        ))}
      </div>

      {!online && b.pending && <div className="banner ok" style={{ borderRadius: 12, margin: '16px 0' }}>Your request for the <b>{b.plans.find((p) => p.id === b.pending!.planId)?.name}</b> plan is being processed.</div>}

      <div className="grid" style={{ marginTop: 16 }}>
        {b.plans.map((p) => {
          const current = p.id === b.plan.id;
          const up = p.priceUsd > b.plan.priceUsd;
          return (
            <div className={`card plan2${current ? ' current' : ''}`} key={p.id}>
              <div className="row between"><b>{p.name}</b>{current && <span className="badge ok">Current</span>}</div>
              <div className="bigprice">{price(p)}<small>{p.priceUsd ? ' / month' : ''}</small></div>
              <ul className="muted"><li><b>{p.maxInstances}</b> WhatsApp number{p.maxInstances > 1 ? 's' : ''}</li><li>{p.maxMsgsDay.toLocaleString()} messages / day</li><li>{p.maxMembers} team seat{p.maxMembers > 1 ? 's' : ''}</li><li>{p.aiCallsDay.toLocaleString()} AI drafts / day</li></ul>
              {!current && owner && p.priceUsd > 0 && online && <button className="primary" disabled={!!busy} onClick={() => subscribe(p)}>{busy === p.id ? 'Please wait…' : subscribed ? (up ? 'Upgrade' : 'Switch to this plan') : 'Subscribe'}</button>}
              {!current && owner && p.priceUsd > 0 && !online && <button className="primary" disabled={!!b.pending} onClick={() => requestUpgrade(p)}>{up ? 'Request upgrade' : 'Request change'}</button>}
              {!current && owner && p.priceUsd === 0 && <small className="muted">To go back to Trial, cancel in Manage billing.</small>}
            </div>
          );
        })}
      </div>
      {!online && owner && !b.pending && <label className="stack" style={{ marginTop: 14 }}>Anything we should know? (optional)<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} placeholder="e.g. I need 5 numbers for my agency" /></label>}
      {!owner && <p className="muted" style={{ marginTop: 14 }}>Only the workspace owner can change the plan.</p>}
      <p className="muted" style={{ marginTop: 18 }}>{online
        ? 'Cards are processed by Stripe in US dollars; ConnectFlow never sees your card number. Upgrades apply immediately (prorated); cancelling keeps your plan until the period ends. PKR prices are indicative only.'
        : 'Payments are arranged directly (bank transfer, JazzCash/Easypaisa, or invoice). After you request a change we confirm the payment and switch your plan, usually within one business day.'}</p>

      {b.history.length > 0 && <><h3 style={{ marginTop: 24 }}>Plan requests</h3>
        <div className="card table-wrap"><table><tbody>{b.history.map((h) => <tr key={h.id}><td>{b.plans.find((p) => p.id === h.planId)?.name}</td><td><span className={`badge ${h.status === 'approved' ? 'ok' : h.status === 'rejected' ? 'bad' : 'warn'}`}>{h.status}</span></td><td className="muted">{new Date(h.createdAt).toLocaleDateString()}</td></tr>)}</tbody></table></div></>}
    </section>
  );
}
