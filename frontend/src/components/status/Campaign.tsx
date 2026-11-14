import { useState } from 'react';
import { api } from '../../api';
import {
  Campaign, Capabilities, clock, errText, fmtDay, FREQ, Notify, PLAN_WORDS, perDayFor, StatusData, THEME_NAME, themeColor, tomorrowIn, useElapsed, WRITE_WORDS,
} from './shared';

type Draft = { brief: string; goal: string; days: string; freq: string; windowFrom: string; windowTo: string; startDate: string; language: string; style: string; name: string };

export function CampaignForm({ data, canEdit, notify, onCreated }: { data: StatusData; canEdit: boolean; notify: Notify; onCreated: (c: Campaign) => void }) {
  const cap = data.capabilities;
  const allowed = cap.plan.campaigns;
  const [v, setV] = useState<Draft>({ brief: '', goal: 'awareness', days: '14', freq: 'i24', windowFrom: '10:00', windowTo: '21:00', startDate: tomorrowIn(cap.timezone), language: data.profile.language || 'English', style: 'card', name: '' });
  const [busy, setBusy] = useState(false);
  const set = (k: keyof Draft, x: string) => setV((s) => ({ ...s, [k]: x }));
  const per = perDayFor(v.freq, v.windowFrom, v.windowTo);
  const n = per * Number(v.days);

  async function plan() {
    if (!v.brief.trim()) { notify('Describe what the campaign should promote', 'bad'); return; }
    if (!per) { notify('The latest post time must be after the earliest', 'bad'); return; }
    setBusy(true);
    try {
      const body: Record<string, unknown> = { brief: v.brief, goal: v.goal, days: Number(v.days), window_from: v.windowFrom, window_to: v.windowTo, start_date: v.startDate, language: v.language, style: v.style, name: v.name };
      if (v.freq.startsWith('i')) body.interval_hours = Number(v.freq.slice(1)); else body.per_day = Number(v.freq.slice(1));
      const r = await api<{ campaign: Campaign }>('/api/status/campaigns', 'POST', body);
      onCreated(r.campaign);
    } catch (e) { notify(errText(e), 'bad'); } finally { setBusy(false); }
  }

  return (
    <div className="stack">
      {!allowed && <div className="st-note amber">Campaigns are not part of your current plan. Upgrade in Billing to plan a whole month of posts at once. You can still create single posts in the Create tab.</div>}
      <fieldset className="fs st-fs" disabled={!canEdit || !allowed}>
        <div className="stack">
          <label>What should this campaign promote?<textarea rows={3} maxLength={1200} value={v.brief} onChange={(e) => set('brief', e.target.value)} placeholder="e.g. Promote our AI chatbot and WhatsApp automation to small businesses; get them to book a free consultation" /></label>
          <div className="st-cols">
            <label>Goal<select value={v.goal} onChange={(e) => set('goal', e.target.value)}>{[['awareness', 'Be known (awareness)'], ['leads', 'Get enquiries (leads)'], ['offer', 'Sell an offer'], ['trust', 'Build trust']].map(([k, nm]) => <option key={k} value={k}>{nm}</option>)}</select></label>
            <label>How long<select value={v.days} onChange={(e) => set('days', e.target.value)}>{[7, 14, 21, 30].map((d) => <option key={d} value={d}>{d} days</option>)}</select></label>
            <label>How often<select value={v.freq} onChange={(e) => set('freq', e.target.value)}>{FREQ.map(([k, nm]) => <option key={k} value={k}>{nm}</option>)}</select></label>
          </div>
          <div className="st-cols">
            <label>Earliest post<input type="time" value={v.windowFrom} onChange={(e) => set('windowFrom', e.target.value)} /></label>
            <label>Latest post<input type="time" value={v.windowTo} onChange={(e) => set('windowTo', e.target.value)} /></label>
            <label>First day<input type="date" value={v.startDate} onChange={(e) => set('startDate', e.target.value)} /></label>
          </div>
          <div className="st-cols">
            <label>Language<select value={v.language} onChange={(e) => set('language', e.target.value)}>{['English', 'Roman Urdu', 'Urdu'].map((l) => <option key={l}>{l}</option>)}</select></label>
            <label>Pictures<select value={v.style} onChange={(e) => set('style', e.target.value)}><option value="card">Designed cards (free)</option><option value="ai" disabled={!cap.aiImages}>{cap.aiImages ? 'AI pictures (uses your image quota)' : 'AI pictures are not set up yet'}</option></select></label>
            <label>Name (optional)<input maxLength={80} value={v.name} onChange={(e) => set('name', e.target.value)} placeholder="e.g. November chatbot push" /></label>
          </div>
          <p className="muted st-small">
            {!per ? <span className="st-bad">The latest post time must be after the earliest.</span> : <>≈ <b>{n} posts</b> ({per} a day for {v.days} days).</>}
            {per > 3 && <span className="st-warn"> More than 3 a day can feel spammy to your contacts.</span>}
            {n > 150 && <span className="st-bad"> Too many: the limit is 150.</span>}
            {per > cap.plan.postsPerDay && <span className="st-warn"> Your plan allows {cap.plan.postsPerDay} posts a day.</span>}
          </p>
          <div className="row end"><button type="button" className="primary" disabled={busy} onClick={plan}>{busy ? 'Starting…' : 'Plan with AI'}</button></div>
          <small className="muted">The AI decides the themes and writes everything from your company data. It asks a question only if it truly cannot plan without it. Nothing is posted until you press <b>Approve all</b>.</small>
        </div>
      </fieldset>
    </div>
  );
}

function ThemeChips({ c }: { c: Campaign }) {
  const agg: Record<string, number> = {};
  for (const s of c.slots) if (s.theme) agg[s.theme] = (agg[s.theme] || 0) + 1;
  return <div className="st-chips">{Object.entries(agg).map(([k, n]) => <span key={k} className="st-chip" style={{ borderLeft: `4px solid ${themeColor(k)}` }}>{THEME_NAME[k] ?? k} · {n}</span>)}</div>;
}

function Busy({ c, words, extra }: { c: Campaign; words: string[]; extra: string }) {
  const since = c.busySince ? new Date(c.busySince).getTime() : Date.now();
  const [t0] = useState(since);
  const sec = useElapsed(t0);
  return (
    <div className="st-busy">
      <div className="st-spin" aria-hidden />
      <div><b>{words[Math.floor(sec / 5) % words.length]}</b><br /><small className="muted">{clock(sec)} · {extra}</small></div>
    </div>
  );
}

/** One campaign, whatever state it is in. The parent polls GET /campaigns/{id} while it is planning/generating and swaps `c` in. */
export function CampaignPanel({ c, cap, canEdit, notify, onChanged, onNew, onGone }: {
  c: Campaign; cap: Capabilities; canEdit: boolean; notify: Notify; onChanged: () => void; onNew: () => void; onGone: () => void;
}) {
  const [busy, setBusy] = useState('');
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [instr, setInstr] = useState('');
  const [hint, setHint] = useState(false);
  const tz = cap.timezone;

  async function call<T = unknown>(key: string, path: string, method = 'POST', body?: unknown, ok?: (r: T) => string) {
    setBusy(key);
    try { const r = await api<T>(`/api/status/campaigns/${c.id}${path}`, method, body); if (ok) notify(ok(r), 'ok'); onChanged(); return true; } catch (e) { notify(errText(e), 'bad'); return false; } finally { setBusy(''); }
  }
  const approve = async () => {
    const n = c.counts.draft || 0;
    if (!confirm(`Approve all ${n} posts?\n\nThey will be scheduled and posted automatically at their planned times${cap.dryRun ? ' (Dry run is ON, so nothing will actually be sent to WhatsApp)' : ''}. You can pause or cancel at any time.`)) return;
    await call<{ scheduled: number; skippedLate: number }>('approve', '/approve', 'POST', undefined, (r: { scheduled: number; skippedLate: number }) => `${r.scheduled} posts scheduled${r.skippedLate ? `, ${r.skippedLate} skipped (time already passed)` : ''}`);
  };
  const cancel = async () => { if (confirm('Cancel this campaign?\n\nEvery post that has not gone out yet is cancelled.')) await call('cancel', '/cancel'); };
  const remove = async () => {
    if (!confirm('Remove this campaign?\n\nUnposted drafts of it are deleted. Posts already sent stay in the list.')) return;
    if (await call('remove', '', 'DELETE')) onGone();
  };
  const dis = !!busy || !canEdit;

  const head = (
    <div className="row between st-gap">
      <div><b>{c.name}</b> <span className={`st-chip cs-${c.state}`}>{c.state}</span><br /><small className="muted">{c.days} days · {c.total} posts · up to {c.maxPerDay} a day · {c.language}</small></div>
      <button type="button" onClick={onNew} disabled={!canEdit}>New campaign</button>
    </div>
  );
  const dry = cap.dryRun && <div className="st-note amber">Dry run is ON: approved posts will NOT reach WhatsApp. It is the “dry run” switch in your <b>Outreach → Setup</b> settings; turn it off there when you are ready.</div>;
  const err = c.error && <div className="st-note red">{c.error}</div>;
  const first = c.slots[0], last = c.slots[c.slots.length - 1];

  let body;
  switch (c.state) {
    case 'planning':
      body = (<><Busy key={c.state} c={c} words={PLAN_WORDS} extra="the AI is researching your plan" /><div className="st-progress"><i className="indet" /></div>
        <small className="muted">A reasoning model thinks carefully: usually 1 to 3 minutes. You can leave this page, it keeps working in the background.</small></>);
      break;
    case 'asking':
      body = (<>{err}<div className="st-note blue">The AI needs {c.questions.length} quick answer{c.questions.length === 1 ? '' : 's'} before it can plan. Leave a box empty to let it decide.</div>
        <div className="stack">{c.questions.map((q) => (
          <label key={q.id}>{q.question}<input list={`dl_${q.id}`} placeholder={q.options.join(' / ')} value={answers[q.id] ?? ''} onChange={(e) => setAnswers((a) => ({ ...a, [q.id]: e.target.value }))} disabled={!canEdit} />
            <datalist id={`dl_${q.id}`}>{q.options.map((o) => <option key={o} value={o} />)}</datalist></label>
        ))}</div>
        <div className="row end st-gap"><button className="primary" disabled={dis} onClick={() => call('answer', '/answer', 'POST', { answers })}>{busy === 'answer' ? 'Starting…' : 'Continue'}</button></div></>);
      break;
    case 'planned':
      body = (<>{err}<div className="st-note green"><b>Roadmap ready.</b> {c.summary}<div className="st-gap"><ThemeChips c={c} /></div></div>
        <div className="table-wrap st-road"><table><thead><tr><th>#</th><th>When</th><th>Theme</th><th>What it says</th></tr></thead><tbody>
          {c.slots.map((s) => <tr key={s.n}><td>{s.n}</td><td>{fmtDay(s.at, tz)} {s.time}</td><td><span className="st-chip" style={{ borderLeft: `4px solid ${themeColor(s.theme)}` }}>{THEME_NAME[s.theme] ?? (s.theme || '…')}</span></td><td className="st-wrap">{s.angle}</td></tr>)}
        </tbody></table></div>
        <div className="st-actions">
          <button className="primary" disabled={dis} onClick={() => call('gen', '/generate', 'POST', undefined, () => 'Writing started: this takes a minute or two')}>Write all {c.total} posts</button>
          <label className="st-grow">Want it different?<input value={instr} onChange={(e) => setInstr(e.target.value)} disabled={!canEdit} placeholder="e.g. more tips, fewer greetings, shorter" /></label>
          <button disabled={dis} onClick={() => call('replan', '/replan', 'POST', { instruction: instr })}>{busy === 'replan' ? 'Starting…' : 'Re-plan'}</button>
          <button className="danger" disabled={dis} onClick={remove}>Discard</button>
        </div></>);
      break;
    case 'generating': {
      const pct = c.total ? Math.round((c.generated / c.total) * 100) : 0;
      body = (<><Busy key={c.state} c={c} words={WRITE_WORDS} extra={`${c.generated}/${c.total} posts ready`} /><div className="st-progress"><i style={{ width: `${pct}%` }} /></div><small className="muted">You can leave this page: it keeps going in the background.</small></>);
      break;
    }
    case 'review':
      body = (<>{dry}{err}<div className="st-note green"><b>{c.counts.draft || 0} posts are written and waiting for your approval.</b><div className="st-gap"><ThemeChips c={c} /></div>
        {first && last && <small>First post: {fmtDay(first.at, tz)} {first.time} · last: {fmtDay(last.at, tz)}. Click any post in the calendar below to look at it, edit it or rewrite it.</small>}</div>
        <div className="st-actions"><button className="primary" disabled={dis} onClick={approve}>{busy === 'approve' ? 'Scheduling…' : `Approve all (${c.counts.draft || 0})`}</button>
          <button disabled={!canEdit} onClick={() => setHint((x) => !x)}>Change something</button><button className="danger" disabled={dis} onClick={cancel}>Cancel campaign</button></div>
        {hint && <p className="muted st-small">Click any post in the calendar to edit or rewrite it. To change the whole plan, cancel this campaign and start a new one.</p>}</>);
      break;
    case 'running':
    case 'paused': {
      const posted = c.counts.posted || 0, left = (c.counts.scheduled || 0) + (c.counts.draft || 0), pct = c.total ? Math.round((posted / c.total) * 100) : 0;
      body = (<>{dry}<p>{posted} posted · {c.counts.scheduled || 0} scheduled{c.counts.failed ? <> · <span className="st-bad">{c.counts.failed} failed</span></> : null} · {left} to go{c.state === 'paused' ? ' · paused' : ''}</p>
        <div className="st-progress"><i style={{ width: `${pct}%` }} /></div>
        <div className="st-actions">{c.state === 'running' ? <button disabled={dis} onClick={() => call('pause', '/pause')}>Pause</button> : <button className="primary" disabled={dis} onClick={() => call('resume', '/resume')}>Resume</button>}
          <button className="danger" disabled={dis} onClick={cancel}>Cancel</button></div></>);
      break;
    }
    case 'failed': {
      const planned = c.slots.some((s) => s.theme);
      body = (<>{err}<div className="st-actions">
        {planned ? <button className="primary" disabled={dis} onClick={() => call('gen', '/generate', 'POST', undefined, () => 'Writing started: this takes a minute or two')}>Write posts (continue)</button>
          : <button className="primary" disabled={dis} onClick={() => call('replan', '/replan', 'POST', { instruction: '' })}>Plan again</button>}
        <button className="danger" disabled={dis} onClick={remove}>Discard</button></div></>);
      break;
    }
    default:
      body = (<><p className="muted">This campaign is {c.state}: {c.counts.posted || 0} post(s) went out.</p>{err}<div className="row"><button disabled={dis} onClick={remove}>Remove from the list</button></div></>);
  }
  return <div className="stack">{head}{body}</div>;
}
