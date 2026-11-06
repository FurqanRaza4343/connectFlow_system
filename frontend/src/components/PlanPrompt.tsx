import { useEffect, useState } from 'react';
import { api } from '../api';

const KEY = 'cf_plan';
const PLANS = ['starter', 'growth'];

/** The pricing page links to /?plan=starter#signup. Remember the choice (survives signup + email verification). */
export function rememberPlanFromUrl() {
  try {
    const p = new URLSearchParams(location.search).get('plan');
    if (p && PLANS.includes(p)) localStorage.setItem(KEY, p);
  } catch { /* storage blocked: the visitor simply picks a plan in Billing */ }
}

/** After the first verified login, offer Checkout for the remembered plan. Nothing is charged until the owner clicks. */
export function PlanPrompt({ onGo }: { onGo: (h: string) => void }) {
  const [plan, setPlan] = useState<{ id: string; name: string; priceUsd: number } | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let id: string | null = null;
    try { id = localStorage.getItem(KEY); } catch { /* ignore */ }
    if (!id) return;
    api<{ plans: Array<{ id: string; name: string; priceUsd: number }>; stripe: { enabled: boolean } }>('/api/billing')
      .then((b) => { const p = b.plans.find((x) => x.id === id); if (p && b.stripe.enabled) setPlan(p); else localStorage.removeItem(KEY); })
      .catch(() => { /* offer is optional */ });
  }, []);
  if (!plan) return null;
  const dismiss = () => { try { localStorage.removeItem(KEY); } catch { /* ignore */ } setPlan(null); };
  const go = async () => {
    setErr('');
    try { const r = await api<{ url?: string }>('/api/billing/checkout', 'POST', { plan_id: plan.id }); try { localStorage.removeItem(KEY); } catch { /* ignore */ } if (r.url) location.href = r.url; else onGo('billing'); }
    catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="banner ok plan-prompt">
      <span>You picked the <b>{plan.name}</b> plan (${plan.priceUsd}/month). Continue to secure checkout whenever you are ready. You are not charged until you confirm on the next page.</span>
      <span className="row"><button className="primary" onClick={go}>Continue to checkout</button><button onClick={dismiss}>Not now</button></span>
      {err && <small className="err">{err}</small>}
    </div>
  );
}
