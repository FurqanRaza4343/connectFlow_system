import { useCallback, useState } from 'react';
import { api, Me } from '../api';
import { ActivityChart, Donut, Funnel, Pt, Slice, Sparkline } from '../components/Charts';
import { ago, countdown, num, usePoll, useNow } from '../components/util';

type Inst = { id: string; label: string; status: string; phone: string; warmupCap: number; sentToday: number; sent7d: number; replies7d: number; outreachEnabled: boolean };
type Sched = {
  autoRun: boolean; dryRun: boolean; requireApproval: boolean; inActiveHours: boolean; nextRunAt: string | null;
  lastBatch: { id: string; at: string; processed: number; queued: number; sent: number; failed: number } | null;
  sentToday: number; dailyLimit: number; eligibleLeads: number; instances: Inst[];
};
type Ov = {
  counts: Record<string, number>; sentToday: number; dailyLimit: number; replyRate: number; pendingApprovals: number; needsHuman: number; unreadNotifications: number;
  daily: Pt[]; scheduler: Sched; recent: { at: string; type: string; title: string; body: string; leadId: string | null }[];
};

const STATUS: Array<[string, string, string]> = [
  ['new', 'New', '#94a3b8'], ['queued', 'Queued', '#a78bfa'], ['contacted', 'Contacted', 'var(--brand)'], ['replied', 'Replied', '#f59e0b'],
  ['verify', 'Verify', '#38bdf8'], ['hold', 'On hold', '#64748b'], ['no_whatsapp', 'No WhatsApp', '#fb7185'], ['failed', 'Failed', '#dc2626'], ['stop', 'Opted out', '#ef4444'],
];
const P = {  // 24x24 line icons
  leads: 'M9 11a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6M17 11a3 3 0 100-6M18.5 14.4c2 .6 3.5 2.4 3.5 5.6',
  send: 'M22 2L11 13M22 2l-7 20-4-9-9-4z', reply: 'M21 12a8 8 0 01-11.7 7L3 21l2-5.5A8 8 0 1121 12z', today: 'M12 7v5l3 2M12 3a9 9 0 100 18 9 9 0 000-18z',
  approve: 'M5 12.5l4.5 4.5L19 7.5', human: 'M12 8v4m0 4h.01M10.3 3.9L2.4 17.5A2 2 0 004.2 20.5h15.6a2 2 0 001.8-3L13.7 3.9a2 2 0 00-3.4 0z',
  batch: 'M3 7l9-4 9 4-9 4-9-4zM3 12l9 4 9-4M3 17l9 4 9-4', session: 'M7 2h10a2 2 0 012 2v16a2 2 0 01-2 2H7a2 2 0 01-2-2V4a2 2 0 012-2z', info: 'M12 8h.01M11 12h1v5h1M12 3a9 9 0 100 18 9 9 0 000-18z',
};
const Ic = ({ d, size = 18 }: { d: string; size?: number }) => <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>;
const FEED_ICON: Record<string, [string, string]> = { batch: [P.batch, 'brand'], reply: [P.reply, 'amber'], needs_human: [P.human, 'red'], failed: [P.human, 'red'], session: [P.session, 'red'], info: [P.info, 'slate'] };
const INST_TONE: Record<string, string> = { ready: 'ok', qr_ready: 'warn', initializing: 'warn', authenticating: 'warn' };

export function Overview({ me, go }: { me: Me; go: (hash: string) => void }) {
  const [d, setD] = useState<Ov | null>(null);
  const [setupDone, setSetupDone] = useState<boolean | null>(null);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState('');
  const now = useNow(1000);
  const canRun = ['owner', 'admin', 'operator'].includes(me.role);
  const canPause = ['owner', 'admin'].includes(me.role);

  const load = useCallback(() => { api<Ov>('/api/overview').then((r) => { setD(r); setErr(''); }).catch((e) => setErr(e.message)); }, []);
  usePoll(load, 20000);
  usePoll(() => { api<{ offer: string; knowledge: string }>('/api/outreach/profile').then((p) => setSetupDone(!!(p.offer.trim() || p.knowledge.trim()))).catch(() => setSetupDone(null)); }, 60000);

  async function runNow() {
    setBusy('run'); setErr(''); setMsg('');
    try {
      const r = await api<{ processed: number; queued: number; sent: number; failed: number }>('/api/outreach/run-now', 'POST', {});
      setMsg(`Batch finished: ${r.processed} processed, ${r.queued} waiting for approval, ${r.sent} sent${r.failed ? `, ${r.failed} failed` : ''}.`); load();
    } catch (e: any) { setErr(e.message); } finally { setBusy(''); }
  }
  async function toggleRun() {
    if (!d) return;
    setBusy('pause'); setErr(''); setMsg('');
    try {
      const p = await api<Record<string, unknown>>('/api/outreach/profile');
      await api('/api/outreach/profile', 'PUT', { ...p, auto_run: !d.scheduler.autoRun });
      setMsg(d.scheduler.autoRun ? 'Scheduler paused.' : 'Scheduler resumed.'); load();
    } catch (e: any) { setErr(e.message); } finally { setBusy(''); }
  }

  if (!d) return <section>{err ? <div className="err">{err}</div> : <div className="stack"><div className="skeleton" style={{ height: 40, width: 220 }} /><div className="ov-kpis">{[0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="skeleton" style={{ height: 112, borderRadius: 16 }} />)}</div><div className="skeleton" style={{ height: 280, borderRadius: 16 }} /></div>}</section>;

  const c = d.counts, s = d.scheduler;
  const slices: Slice[] = STATUS.map(([key, label, color]) => ({ key, label, value: c[key] || 0, color }));
  const reached = (c.contacted || 0) + (c.replied || 0) + (c.stop || 0);
  const pct = d.dailyLimit ? Math.min(100, Math.round((d.sentToday / d.dailyLimit) * 100)) : 0;
  const state: [string, string] = s.dryRun ? ['Dry-run', 'warn'] : !s.autoRun ? ['Paused', 'warn'] : s.inActiveHours ? ['Running', 'ok'] : ['Waiting', 'warn'];
  const sentSeries = d.daily.map((x) => x.sent), repSeries = d.daily.map((x) => x.replies);
  const sum14 = (a: number[]) => a.reduce((x, y) => x + y, 0);
  const disconnected = s.instances.filter((i) => i.status !== 'ready');
  const attention: Array<{ key: string; text: string; sub: string; go: string; tone: string }> = [];
  if (d.needsHuman) attention.push({ key: 'nh', text: `${d.needsHuman} repl${d.needsHuman === 1 ? 'y needs' : 'ies need'} you`, sub: 'Pricing or sensitive questions', go: 'outreach/inbox', tone: 'red' });
  if (d.pendingApprovals) attention.push({ key: 'pa', text: `${d.pendingApprovals} message${d.pendingApprovals === 1 ? '' : 's'} to approve`, sub: 'Review and send', go: 'outreach/approvals', tone: 'amber' });
  if (c.failed) attention.push({ key: 'fl', text: `${c.failed} lead${c.failed === 1 ? '' : 's'} failed`, sub: 'A send did not go through', go: 'outreach/leads', tone: 'red' });
  disconnected.forEach((i) => attention.push({ key: i.id, text: `${i.label} is ${i.status.replace('_', ' ')}`, sub: 'Reconnect the number', go: 'numbers', tone: 'red' }));

  const steps = [
    { done: s.instances.some((i) => i.status === 'ready'), label: 'Connect a WhatsApp number', hint: 'Scan the QR code once.', go: 'numbers' },
    { done: setupDone === true, label: 'Fill in your outreach setup', hint: 'Tell the AI what you sell.', go: 'outreach/setup' },
    { done: (c.total || 0) > 0, label: 'Import your leads', hint: 'Upload a CSV.', go: 'outreach/leads' },
    { done: !!s.lastBatch, label: 'Run your first batch', hint: 'Drafts appear in Approvals.', go: 'outreach/approvals' },
  ];
  const showChecklist = setupDone !== null && steps.some((x) => !x.done);
  const first = (me.user.name || '').split(' ')[0];
  const hour = new Date().getHours();

  return (
    <section className="ov">
      <div className="ov-head">
        <div><h2>{hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'}{first ? `, ${first}` : ''}</h2><p className="muted">Here is what your outreach is doing right now.</p></div>
        <div className="row">
          <span className={`badge ${state[1]}`}><i className="dot" />{state[0]}</span>
          {canRun && <button className="primary" disabled={busy === 'run'} onClick={runNow}>{busy === 'run' ? 'Running…' : 'Run batch now'}</button>}
          {canPause && <button disabled={busy === 'pause'} onClick={toggleRun}>{s.autoRun ? 'Pause' : 'Resume'}</button>}
        </div>
      </div>
      {err && <div className="err">{err}</div>}{msg && <div className="okbox">{msg}</div>}
      {s.dryRun && <div className="ov-note"><span><b>Dry-run is ON.</b> Messages are written for you to review, but nothing is sent until you turn it off.</span><button onClick={() => go('outreach/setup')}>Open Setup</button></div>}

      {showChecklist && (
        <div className="ov-card">
          <div className="ov-title">Get started <small className="muted">{steps.filter((x) => x.done).length} of {steps.length} done</small></div>
          <div className="steps2">{steps.map((x, i) => (
            <button key={i} className={`step2${x.done ? ' done' : ''}`} onClick={() => go(x.go)}>
              <span className="tick">{x.done ? '✓' : i + 1}</span><span><b>{x.label}</b><small className="muted">{x.hint}</small></span>
            </button>))}</div>
        </div>
      )}

      <div className="ov-kpis">
        <Kpi icon={P.leads} tone="slate" label="Total leads" value={num(c.total)} sub={`${num(c.withNumber)} with a WhatsApp number`} onClick={() => go('outreach/leads')} />
        <Kpi icon={P.send} tone="brand" label="Contacted" value={num(reached)} sub={`${num(c.new)} still to contact`} spark={sentSeries} />
        <Kpi icon={P.reply} tone="amber" label="Replied" value={num(c.replied)} sub={`${d.replyRate}% reply rate`} spark={repSeries} sparkColor="#f59e0b" onClick={() => go('outreach/inbox')} />
        <Kpi icon={P.today} tone="brand" label="Sent today" value={`${d.sentToday}`} suffix={`/ ${d.dailyLimit}`} bar={pct} />
        <Kpi icon={P.approve} tone="amber" label="To approve" value={num(d.pendingApprovals)} sub="messages waiting" warn={d.pendingApprovals > 0} onClick={() => go('outreach/approvals')} />
        <Kpi icon={P.human} tone="red" label="Needs you" value={num(d.needsHuman)} sub="pricing / sensitive" warn={d.needsHuman > 0} onClick={() => go('outreach/inbox')} />
      </div>

      <div className="ov-row r21">
        <div className="ov-card">
          <div className="ov-title">Activity · last 14 days
            <span className="ov-legend"><i style={{ background: 'var(--brand)' }} />Sent <b>{sum14(sentSeries)}</b><i style={{ background: '#f59e0b' }} />Replies <b>{sum14(repSeries)}</b></span></div>
          <ActivityChart data={d.daily} />
        </div>
        <div className="ov-card">
          <div className="ov-title">Pipeline <small className="muted">conversion between steps</small></div>
          <Funnel steps={[
            { label: 'Leads', value: c.total || 0, color: '#64748b' },
            { label: 'With a number', value: c.withNumber || 0, color: '#38bdf8' },
            { label: 'Contacted', value: reached, color: 'var(--brand)' },
            { label: 'Replied', value: c.replied || 0, color: '#f59e0b' },
          ]} />
          <p className="muted ov-foot">Reply rate counts everyone who answered, including opt-outs.</p>
        </div>
      </div>

      <div className="ov-row r111">
        <div className="ov-card">
          <div className="ov-title">Scheduler</div>
          <div className="ov-facts">
            <div><small>Next batch</small><b>{s.autoRun && !s.dryRun ? (s.nextRunAt ? countdown(s.nextRunAt, now) : s.inActiveHours ? 'any moment' : 'next active hours') : '—'}</b></div>
            <div><small>Active hours</small><b>{s.inActiveHours ? 'Inside now' : 'Outside now'}</b></div>
            <div><small>Ready to contact</small><b>{num(s.eligibleLeads)} leads</b></div>
            <div><small>Last batch</small><b>{s.lastBatch ? ago(s.lastBatch.at) : 'never'}</b></div>
          </div>
          {s.lastBatch && <p className="muted ov-foot">{s.lastBatch.processed} processed · {s.lastBatch.queued} queued · {s.lastBatch.sent} sent{s.lastBatch.failed ? ` · ${s.lastBatch.failed} failed` : ''}</p>}
          <button className="link" onClick={() => go('outreach/setup')}>Schedule settings →</button>
        </div>

        <div className="ov-card">
          <div className="ov-title">WhatsApp numbers <button className="link" onClick={() => go('numbers')}>Manage →</button></div>
          {s.instances.map((i) => {
            const p = i.warmupCap ? Math.min(100, Math.round((i.sentToday / i.warmupCap) * 100)) : 0;
            return (
              <div className="ov-num" key={i.id}>
                <div className="row between"><b>{i.label}</b><span className="row" style={{ gap: 6 }}><span className={`badge ${i.outreachEnabled ? 'ok' : 'warn'}`}>{i.outreachEnabled ? 'outreach on' : 'API only'}</span><span className={`badge ${INST_TONE[i.status] || 'bad'}`}>{i.status === 'ready' ? 'Connected' : i.status.replace('_', ' ')}</span></span></div>
                <small className="muted">{i.phone ? `+${i.phone.replace(/\D/g, '')}` : 'not linked yet'}</small>
                <div className="meterbar"><i style={{ width: `${p}%` }} className={p > 85 ? 'hot' : ''} /></div>
                <small className="muted">Today {i.sentToday} of {i.warmupCap} (grows as the number ages) · 7 days: {i.sent7d} sent, {i.replies7d} replies</small>
              </div>
            );
          })}
          {!s.instances.length && <div className="ov-empty"><Ic d={P.session} size={26} /><p>No number connected yet.</p><button className="primary" onClick={() => go('numbers')}>Add a number</button></div>}
        </div>

        <div className="ov-card">
          <div className="ov-title">Needs attention {attention.length > 0 && <span className="badge bad">{attention.length}</span>}</div>
          {attention.map((a) => (
            <button key={a.key} className={`ov-attn ${a.tone}`} onClick={() => go(a.go)}><span className="chipi"><Ic d={a.tone === 'amber' ? P.approve : P.human} size={16} /></span><span><b>{a.text}</b><small>{a.sub}</small></span><span aria-hidden="true">›</span></button>
          ))}
          {!attention.length && <div className="ov-empty"><Ic d={P.approve} size={26} /><p>All clear. Nothing needs you right now.</p></div>}
        </div>
      </div>

      <div className="ov-row r12">
        <div className="ov-card">
          <div className="ov-title">Leads by status</div>
          <div className="donut-wrap">
            <Donut slices={slices} center={{ big: num(c.total), small: 'leads' }} />
            <ul className="legend2">{slices.filter((x) => x.value > 0).map((x) => <li key={x.key}><i style={{ background: x.color }} />{x.label}<b>{x.value}</b></li>)}{!c.total && <li className="muted">No leads yet</li>}</ul>
          </div>
        </div>
        <div className="ov-card">
          <div className="ov-title">Recent activity</div>
          <div className="ov-feed">
            {d.recent.slice(0, 7).map((r, i) => { const [ic, tone] = FEED_ICON[r.type] || FEED_ICON.info; return (
              <button key={i} className="ov-fi" onClick={() => go(r.type === 'session' ? 'numbers' : r.type === 'batch' || r.type === 'failed' ? 'outreach/approvals' : 'outreach/inbox')}>
                <span className={`chipi ${tone}`}><Ic d={ic} size={15} /></span><span><b>{r.title}</b>{r.body && <small className="muted">{r.body.slice(0, 80)}</small>}</span><small className="muted">{ago(r.at)}</small>
              </button>); })}
            {!d.recent.length && <div className="ov-empty"><Ic d={P.batch} size={26} /><p>Activity shows up here once your first batch runs.</p></div>}
          </div>
        </div>
      </div>
    </section>
  );
}

function Kpi({ icon, tone, label, value, suffix, sub, spark, sparkColor, bar, onClick, warn }: { icon: string; tone: string; label: string; value: string; suffix?: string; sub?: string; spark?: number[]; sparkColor?: string; bar?: number; onClick?: () => void; warn?: boolean }) {
  const inner = <>
    <div className="kpi-top"><span className={`chipi ${tone}`}><Ic d={icon} size={17} /></span><small>{label}</small></div>
    <div className="kpi-main"><div className="kpi-v">{value}{suffix && <span className="kpi-s">{suffix}</span>}</div>{spark && <Sparkline values={spark} color={sparkColor} />}</div>
    {bar !== undefined ? <div className="meterbar"><i style={{ width: `${bar}%` }} className={bar > 85 ? 'hot' : ''} /></div> : <small className="kpi-sub">{sub}</small>}
  </>;
  const cls = `ov-kpi${warn ? ' warnk' : ''}`;
  return onClick ? <button className={cls} onClick={onClick}>{inner}</button> : <div className={cls}>{inner}</div>;
}
