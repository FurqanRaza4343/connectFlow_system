import { useEffect, useState } from 'react';
import { api, Me } from '../api';
import { COUNTRY } from './Leads';
import { DEFAULT_MEDIA, MediaCfg, MediaSettings, mediaProblem, normMedia } from './MediaSettings';

type P = {
  company_name: string; sender_name: string; website: string; offer: string; tone: string; knowledge: string; default_country: string;
  timezone: string; active_from: string; active_to: string; daily_limit: number; require_approval: boolean;
  auto_run: boolean; batch_size: number; interval_hours: number; delay_min: number; delay_max: number; dry_run: boolean; auto_reply: boolean;
  hold_pricing: boolean; followups_enabled: boolean; followup_day1: number; followup_day2: number; fallback_template: string;
  notify_email: boolean; notify_extra_emails: string; notify_digest: boolean;
  media: MediaCfg;
};

const DEFAULTS: Partial<P> = { auto_run: false, batch_size: 5, interval_hours: 5, delay_min: 60, delay_max: 180, dry_run: true, auto_reply: false, hold_pricing: true, followups_enabled: false, followup_day1: 3, followup_day2: 7, fallback_template: '', notify_email: true, notify_extra_emails: '', notify_digest: false, media: DEFAULT_MEDIA };

function validate(p: P): string {
  if (p.delay_min > p.delay_max) return 'The shortest gap between messages cannot be longer than the longest gap.';
  if (p.delay_min < 5) return 'Keep at least 5 seconds between messages.';
  if (p.batch_size < 1 || p.batch_size > 100) return 'Leads per batch must be between 1 and 100.';
  if (p.interval_hours < 1 || p.interval_hours > 168) return 'Hours between batches must be between 1 and 168.';
  if (p.followups_enabled && (p.followup_day1 < 1 || p.followup_day2 <= p.followup_day1)) return 'The second follow-up must come later than the first.';
  const extras = p.notify_extra_emails.split(/[,;\s]+/).filter(Boolean);
  if (extras.length > 3) return 'Add at most 3 extra email addresses.';
  if (extras.some((e) => !/^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(e))) return 'One of the extra email addresses is not valid.';
  const mp = mediaProblem(p.media);
  if (mp) return mp;
  if (!/^\d{2}:\d{2}$/.test(p.active_from) || !/^\d{2}:\d{2}$/.test(p.active_to)) return 'Choose valid active hours.';
  return '';
}

export function OutreachSetup({ me, go }: { me: Me; go?: (hash: string) => void }) {
  const [p, setP] = useState<P | null>(null);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const canEdit = ['owner', 'admin'].includes(me.role);
  useEffect(() => { api<P>('/api/outreach/profile').then((r) => setP({ ...DEFAULTS, ...r, media: normMedia((r as any).media) } as P)).catch((e) => setErr(e.message)); }, []);
  if (!p) return err ? <div className="err">{err}</div> : <div className="skeleton" style={{ height: 240 }} />;
  const set = <K extends keyof P>(k: K, v: P[K]) => setP({ ...p, [k]: v });
  const problem = validate(p);
  const save = async () => {
    setErr(''); setMsg('');
    if (problem) { setErr(problem); return; }
    setBusy(true);
    try { { const r = await api<P>('/api/outreach/profile', 'PUT', p); setP({ ...DEFAULTS, ...r, media: normMedia((r as any).media) } as P); } setMsg('Saved.'); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const zones = (Intl as any).supportedValuesOf ? ((Intl as any).supportedValuesOf('timeZone') as string[]) : ['UTC'];
  const sw = (k: keyof P, label: string, hint: string) => (
    <label className="switch-row">
      <span><b>{label}</b><small className="muted">{hint}</small></span>
      <input type="checkbox" role="switch" checked={!!p[k]} onChange={(e) => set(k, e.target.checked as never)} />
    </label>
  );
  const numf = (k: keyof P, label: string, hint: string, min = 1, max = 1000) => (
    <label>{label}<input type="number" min={min} max={max} value={p[k] as number} onChange={(e) => set(k, Number(e.target.value) as never)} /><small className="muted">{hint}</small></label>
  );

  return (
    <div className="stack" style={{ maxWidth: 780 }}>
      <p className="muted" style={{ margin: 0 }}>Tell the AI who you are and what you sell, then decide how and when messages go out.</p>
      {err && <div className="err">{err}</div>}{msg && <div className="okbox">{msg}</div>}
      <fieldset disabled={!canEdit} className="stack fs">
        <div className="card stack"><b>About you</b>
          <div className="two"><label>Your company<input value={p.company_name} onChange={(e) => set('company_name', e.target.value)} /></label><label>Sender name (signs the messages)<input value={p.sender_name} onChange={(e) => set('sender_name', e.target.value)} placeholder="e.g. Ayesha" /></label></div>
          <label>Website<input value={p.website} onChange={(e) => set('website', e.target.value)} placeholder="yourcompany.com" /></label>
          <label>What do you offer? <small className="muted">2–4 sentences, plain language</small><textarea rows={4} value={p.offer} onChange={(e) => set('offer', e.target.value)} placeholder="We help dental clinics reduce no-shows with automated WhatsApp reminders…" /></label>
          <label>Knowledge the AI may use <small className="muted">services, proof points, pricing rules, things to never say</small><textarea rows={6} value={p.knowledge} onChange={(e) => set('knowledge', e.target.value)} /></label>
          <div className="two"><label>Tone<select value={p.tone} onChange={(e) => set('tone', e.target.value)}><option value="friendly">Friendly</option><option value="formal">Formal</option><option value="short">Short and direct</option></select></label>
            <label>Default country<select value={p.default_country} onChange={(e) => set('default_country', e.target.value)}>{Object.entries(COUNTRY).map(([c, n]) => <option key={c} value={c}>{n}</option>)}{!COUNTRY[p.default_country] && <option value={p.default_country}>{p.default_country}</option>}</select><small className="muted">For numbers written without a country code.</small></label></div>
        </div>

        <div className="card stack"><b>Schedule</b>
          {sw('auto_run', 'Run automatically', 'The scheduler drafts a batch of messages on its own, around the clock, but only sends inside your active hours. Off = paused.')}
          <div className="two">{numf('batch_size', 'Leads per batch', 'How many new leads are researched and drafted each time.', 1, 100)}{numf('interval_hours', 'Hours between batches', 'A calm rhythm looks more human than one big blast.', 1, 168)}</div>
          <div className="two"><label>Active from<input type="time" value={p.active_from} onChange={(e) => set('active_from', e.target.value)} /></label><label>Active until<input type="time" value={p.active_to} onChange={(e) => set('active_to', e.target.value)} /></label></div>
          <label>Timezone<input list="zones" value={p.timezone} onChange={(e) => set('timezone', e.target.value)} /><datalist id="zones">{zones.map((z) => <option key={z} value={z} />)}</datalist><small className="muted">Active hours are measured in this timezone.</small></label>
          <div className="two">{numf('delay_min', 'Shortest gap (seconds)', 'Minimum pause between two messages.', 5, 3600)}{numf('delay_max', 'Longest gap (seconds)', 'A random gap in between keeps your number safe.', 5, 3600)}</div>
          {numf('daily_limit', 'Max first messages per day', 'Hard cap for the whole workspace. New numbers also follow an automatic warm-up.', 1, 500)}
        </div>

        <div className="card stack"><b>Safety</b>
          {sw('dry_run', 'Dry-run', 'Write messages for you to review but never send them. Turn this off when you are ready to go live.')}
          {sw('require_approval', 'First messages need my approval', 'Nothing goes out until you approve it. Strongly recommended.')}
          {sw('auto_reply', 'Auto-reply when a lead answers', 'The AI answers simple replies by itself. Pricing and sensitive questions always wait for you.')}
          {sw('hold_pricing', 'Always hold pricing questions for me', 'Replies about price or quotes are flagged “needs you” instead of being answered by AI.')}
          <label>Fallback message <small className="muted">used only if the AI is unavailable; use {'{{company}}'} and {'{{sender}}'}. Fallback messages are never sent without your review.</small><textarea rows={3} value={p.fallback_template} onChange={(e) => set('fallback_template', e.target.value)} /></label>
        </div>

        <div className="card stack"><b>Follow-ups</b>
          {sw('followups_enabled', 'Send gentle follow-ups', 'If a lead has not replied, the AI drafts a short nudge. It stops the moment they reply, opt out, or you put them on hold.')}
          {p.followups_enabled && <div className="two">{numf('followup_day1', 'First follow-up after (days)', 'Counted from the first message.', 1, 60)}{numf('followup_day2', 'Second follow-up after (days)', 'Must be later than the first.', 2, 120)}</div>}
        </div>
        <MediaSettings value={p.media} onChange={(m) => set('media', m)} canEdit={canEdit} go={go} followupsOn={p.followups_enabled} />
        <div className="card stack"><b>Notifications</b>
          {sw('notify_email', 'Email me about important events', 'A lead needs a human answer, a number disconnects or needs a new QR, a batch has failures, or the daily limit is reached. At most one email per type per hour.')}
          <label>Extra email addresses <small className="muted">optional, up to 3, comma separated. Owners and admins always receive alerts.</small><input value={p.notify_extra_emails} onChange={(e) => set('notify_extra_emails', e.target.value)} placeholder="sales@yourcompany.com, ops@yourcompany.com" disabled={!p.notify_email} /></label>
          {sw('notify_digest', 'Daily summary at 08:00', 'One short email each morning (your workspace timezone): what went out yesterday and what is waiting for you.')}
        </div>
      </fieldset>
      {canEdit ? <div className="row"><button className="primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save setup'}</button>{problem && <small className="muted">{problem}</small>}</div> : <p className="muted">Only owners and admins can change this.</p>}
    </div>
  );
}
