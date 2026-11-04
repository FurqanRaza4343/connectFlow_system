import { useCallback, useEffect, useState } from 'react';
import { api, Me } from '../api';

type Plan = { id: string; name: string; priceUsd: number; pricePkr: number; maxInstances: number; maxMsgsDay: number; maxMembers: number; aiCallsDay: number };
type B = {
  plan: Plan; usage: { messagesToday: number; aiCallsToday: number; numbers: number; members: number }; plans: Plan[];
  pending: { id: string; planId: string; createdAt: string } | null; history: { id: string; planId: string; status: string; createdAt: string }[];
};

export function Billing({ me }: { me: Me }) {
  const [b, setB] = useState<B | null>(null);
  const [cur, setCur] = useState<'USD' | 'PKR'>('USD');
  const [note, setNote] = useState('');
  const [err, setErr] = useState('');
  const [ok, setOk] = useState('');
  const owner = me.role === 'owner';
  const load = useCallback(() => api<B>('/api/billing').then(setB).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const price = (p: Plan) => (p.priceUsd === 0 ? 'Free' : cur === 'USD' ? `$${p.priceUsd}` : `PKR ${p.pricePkr.toLocaleString()}`);

  async function upgrade(p: Plan) {
    setErr(''); setOk('');
    try { await api('/api/billing/upgrade', 'POST', { plan_id: p.id, note }); setOk(`Request sent. We’ll contact you with payment details for the ${p.name} plan.`); setNote(''); load(); }
    catch (e: any) { setErr(e.message); }
  }
  if (!b) return <section>{err ? <div className="err">{err}</div> : <p className="muted">Loading…</p>}</section>;

  const rows: Array<[string, string, number, number]> = [
    ['Messages today', `${b.usage.messagesToday} / ${b.plan.maxMsgsDay}`, b.usage.messagesToday, b.plan.maxMsgsDay],
    ['AI calls today', `${b.usage.aiCallsToday} / ${b.plan.aiCallsDay}`, b.usage.aiCallsToday, b.plan.aiCallsDay],
    ['WhatsApp numbers', `${b.usage.numbers} / ${b.plan.maxInstances}`, b.usage.numbers, b.plan.maxInstances],
    ['Team seats', `${b.usage.members} / ${b.plan.maxMembers}`, b.usage.members, b.plan.maxMembers],
  ];
  return (
    <section>
      <div className="row between"><div><h2>Billing</h2><p className="muted">You’re on the <b>{b.plan.name}</b> plan.</p></div>
        <div className="toggle2"><button className={cur === 'USD' ? 'on' : ''} onClick={() => setCur('USD')}>USD</button><button className={cur === 'PKR' ? 'on' : ''} onClick={() => setCur('PKR')}>PKR</button></div></div>
      {err && <div className="err">{err}</div>}{ok && <div className="okbox">{ok}</div>}

      <div className="card usage">
        {rows.map(([label, text, v, max]) => (
          <div key={label}><div className="row between"><span>{label}</span><span className="muted">{text}</span></div>
            <div className="meterbar"><i style={{ width: `${Math.min(100, (v / Math.max(1, max)) * 100)}%` }} className={v / Math.max(1, max) > 0.85 ? 'hot' : ''} /></div></div>
        ))}
      </div>

      {b.pending && <div className="banner ok" style={{ borderRadius: 12, margin: '16px 0' }}>Your request for the <b>{b.plans.find((p) => p.id === b.pending!.planId)?.name}</b> plan is being processed.</div>}

      <div className="grid" style={{ marginTop: 16 }}>
        {b.plans.map((p) => {
          const current = p.id === b.plan.id;
          return (
            <div className={`card plan2${current ? ' current' : ''}`} key={p.id}>
              <div className="row between"><b>{p.name}</b>{current && <span className="badge ok">Current</span>}</div>
              <div className="bigprice">{price(p)}<small>{p.priceUsd ? ' / month' : ''}</small></div>
              <ul className="muted"><li>{p.maxInstances} WhatsApp number(s)</li><li>{p.maxMsgsDay.toLocaleString()} messages / day</li><li>{p.maxMembers} team seat(s)</li><li>{p.aiCallsDay.toLocaleString()} AI drafts / day</li></ul>
              {!current && owner && <button className="primary" disabled={!!b.pending} onClick={() => upgrade(p)}>{p.priceUsd > b.plan.priceUsd ? 'Request upgrade' : 'Request change'}</button>}
            </div>
          );
        })}
      </div>
      {owner && !b.pending && <label className="stack" style={{ marginTop: 14 }}>Anything we should know? (optional)<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} placeholder="e.g. I need 5 numbers for my agency" /></label>}
      {!owner && <p className="muted" style={{ marginTop: 14 }}>Only the workspace owner can change the plan.</p>}
      <p className="muted" style={{ marginTop: 18 }}>Payments are arranged directly (bank transfer, JazzCash/Easypaisa, or invoice). After you request a change we confirm the payment and switch your plan, usually within one business day.</p>

      {b.history.length > 0 && <><h3 style={{ marginTop: 24 }}>Plan requests</h3>
        <div className="card table-wrap"><table><tbody>{b.history.map((h) => <tr key={h.id}><td>{b.plans.find((p) => p.id === h.planId)?.name}</td><td><span className={`badge ${h.status === 'approved' ? 'ok' : h.status === 'rejected' ? 'bad' : 'warn'}`}>{h.status}</span></td><td className="muted">{new Date(h.createdAt).toLocaleDateString()}</td></tr>)}</tbody></table></div></>}
    </section>
  );
}
