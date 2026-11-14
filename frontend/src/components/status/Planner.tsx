import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../api';
import { Modal } from '../Modal';
import { BarChart, BarTable, Donut } from './Charts';
import { CampaignForm, CampaignPanel } from './Campaign';
import { canAct, Thumb, usePostOps } from './Posts';
import { addMonth, Calendar, Campaign, CalItem, fmtStamp, fmtTime, monthLabel, Notify, Post, STATE_ICON, Stats, StatusData, THEME_NAME } from './shared';

const OPEN_STATES = ['planning', 'asking', 'planned', 'generating', 'review', 'running', 'paused', 'failed'];
const WEEK = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function Kpi({ label, n, sub }: { label: string; n: string | number; sub: string }) {
  return <div className="st-kpi"><small>{label}</small><b>{n}</b><small>{sub}</small></div>;
}

function CalendarGrid({ cal, today, tz, onOpen }: { cal: Calendar; today: string; tz: string; onOpen: (it: CalItem) => void }) {
  const [y, mo] = cal.month.split('-').map(Number);
  const days = new Date(Date.UTC(y, mo, 0)).getUTCDate();
  const lead = (new Date(Date.UTC(y, mo - 1, 1)).getUTCDay() + 6) % 7; // Monday first
  const by = useMemo(() => { const m: Record<string, CalItem[]> = {}; for (const it of cal.items) (m[it.date] ||= []).push(it); return m; }, [cal.items]);
  const cells: JSX.Element[] = [];
  for (let i = 0; i < lead; i++) cells.push(<div key={`o${i}`} className="cal-cell off" />);
  for (let d = 1; d <= days; d++) {
    const date = `${cal.month}-${String(d).padStart(2, '0')}`;
    const items = by[date] ?? [];
    cells.push(
      <div key={date} className={`cal-cell${date === today ? ' today' : ''}`}>
        <div className="cal-d">{d}{items.length > 0 && <span className="cal-n">{items.length}</span>}</div>
        {items.slice(0, 3).map((it) => {
          const [ic, label, col] = STATE_ICON[it.state] ?? STATE_ICON.draft;
          return (
            <button key={it.id} type="button" className="cal-item" style={{ borderLeftColor: col }} title={`${label}: ${it.headline}`} onClick={() => onOpen(it)}>
              <span style={{ color: col }}>{ic}</span> <span className="cal-t">{fmtTime(it.at, tz)}</span> <span className="cal-h">{it.headline}</span>
            </button>
          );
        })}
        {items.length > 3 && <small className="muted cal-more">+{items.length - 3} more</small>}
      </div>,
    );
  }
  return <div className="cal-grid">{WEEK.map((w) => <div key={w} className="cal-w">{w}</div>)}{cells}</div>;
}

export function Planner({ data, canEdit, notify, reloadData, onEdit, onRepost }: {
  data: StatusData; canEdit: boolean; notify: Notify; reloadData: () => void; onEdit: (p: Post) => void; onRepost: (p: Post) => void;
}) {
  const cap = data.capabilities, tz = cap.timezone;
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [cal, setCal] = useState<Calendar | null>(null);
  const [month, setMonth] = useState<string | null>(null); // null = the current month
  const [cid, setCid] = useState<string | null>(null);      // null = the active campaign (if any), 'new' = the form
  const [view, setView] = useState<'chart' | 'table'>('chart');
  const [openId, setOpenId] = useState('');
  const [err, setErr] = useState('');
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => { setTick((t) => t + 1); reloadData(); }, [reloadData]);
  const ops = usePostOps(cap, notify, () => { setOpenId(''); refresh(); });

  // Everything the page shows, reloaded on mount, on month change and whenever something changed.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [cs, st] = await Promise.all([api<{ campaigns: Campaign[] }>('/api/status/campaigns'), api<Stats>('/api/status/stats')]);
        const m = month ?? st.today.slice(0, 7);
        const c = await api<Calendar>(`/api/status/calendar?month=${m}`);
        if (!alive) return;
        setCampaigns(cs.campaigns); setStats(st); setCal(c); setErr('');
      } catch (e) { if (alive) setErr(e instanceof Error ? e.message : 'Could not load the planner'); }
    })();
    return () => { alive = false; };
  }, [month, tick]);

  const current = campaigns ? (cid === 'new' ? null : campaigns.find((c) => c.id === cid) ?? campaigns.find((c) => OPEN_STATES.includes(c.state)) ?? null) : null;
  const curId = current?.id, curState = current?.state;

  // While the worker plans or writes, ask for the campaign every 2 s. The timer lives only as long as this campaign stays in that state on this page.
  useEffect(() => {
    if (!curId || (curState !== 'planning' && curState !== 'generating')) return;
    let alive = true, inflight = false;
    const t = setInterval(async () => {
      if (inflight) return;
      inflight = true;
      try {
        const { campaign } = await api<{ campaign: Campaign }>(`/api/status/campaigns/${curId}`);
        if (!alive) return;
        setCampaigns((list) => (list ? list.map((c) => (c.id === campaign.id ? campaign : c)) : list));
        if (campaign.state !== curState) refresh();
      } catch { /* keep polling */ } finally { inflight = false; }
    }, 2000);
    return () => { alive = false; clearInterval(t); };
  }, [curId, curState, refresh]);

  if (err && !stats) return <div className="card"><p className="st-bad">{err}</p><button onClick={refresh}>Try again</button></div>;
  if (!campaigns || !stats || !cal) return <div className="card"><div className="skeleton" style={{ height: 160 }} /></div>;

  const shown = month ?? stats.today.slice(0, 7);
  const u = stats.usage;
  const todayRow = stats.series.find((s) => s.day === stats.today);
  const open = openId ? data.posts.find((p) => p.id === openId) : undefined;
  const earlier = campaigns.filter((c) => c.id !== current?.id).slice(0, 6);

  function openItem(it: CalItem) {
    if (!data.posts.some((p) => p.id === it.id)) { notify('That post is no longer in the recent list. Open the Posts tab.', 'bad'); return; }
    setOpenId(it.id);
  }

  return (
    <>
      <div className="st-kpis">
        <Kpi label="Posted today" n={todayRow?.posted ?? 0} sub={`${u.today.posted} counted by the engine`} />
        <Kpi label="Scheduled" n={stats.states.scheduled || 0} sub={`next: ${stats.nextAt ? fmtStamp(stats.nextAt, tz) : '—'}`} />
        <Kpi label="Drafts to review" n={stats.states.draft || 0} sub="not posted until approved" />
        <Kpi label="Failed" n={stats.states.failed || 0} sub="open the Posts tab to retry" />
        <Kpi label="AI pictures today" n={`${u.today.images}/${u.caps.imagesPerDay}`} sub={`${u.month.images}/${u.caps.imagesPerMonth} this month`} />
      </div>

      <div className="card st-sect">
        <h2>Campaign planner</h2>
        {current
          ? <CampaignPanel key={current.id} c={current} cap={cap} canEdit={canEdit} notify={notify} onChanged={refresh} onNew={() => setCid('new')} onGone={() => { setCid('new'); refresh(); }} />
          : <CampaignForm data={data} canEdit={canEdit} notify={notify} onCreated={(c) => { setCid(c.id); setCampaigns((l) => [c, ...(l ?? [])]); refresh(); }} />}
        {earlier.length > 0 && (
          <div className="st-chips st-gap"><small className="muted">Earlier:</small>{earlier.map((c) => <button key={c.id} type="button" className="chipb" onClick={() => setCid(c.id)}>{c.name.slice(0, 22)} · {c.state}</button>)}</div>
        )}
      </div>

      <div className="card st-sect">
        <div className="row between st-gap"><h2>Calendar</h2><b>{monthLabel(shown)}</b>
          <span className="row"><button type="button" aria-label="Previous month" onClick={() => setMonth(addMonth(shown, -1))}>‹</button><button type="button" onClick={() => setMonth(null)}>Today</button><button type="button" aria-label="Next month" onClick={() => setMonth(addMonth(shown, 1))}>›</button></span></div>
        <CalendarGrid cal={cal} today={stats.today} tz={tz} onOpen={openItem} />
        <div className="st-legend st-gap">{['posted', 'scheduled', 'draft', 'failed'].map((k) => <span key={k}><b style={{ color: STATE_ICON[k][2] }}>{STATE_ICON[k][0]}</b> {STATE_ICON[k][1]}</span>)}</div>
      </div>

      <div className="st-two st-sect">
        <div className="card">
          <div className="row between"><h2>Posts per day</h2><button type="button" onClick={() => setView(view === 'chart' ? 'table' : 'chart')}>{view === 'chart' ? 'Show as table' : 'Show as chart'}</button></div>
          {view === 'chart' ? <BarChart series={stats.series} today={stats.today} /> : <BarTable series={stats.series} />}
        </div>
        <div className="card"><h2>Content mix</h2><Donut themes={stats.themes} /></div>
      </div>

      {open && (
        <Modal title={open.headline || 'Post'} wide onClose={() => setOpenId('')}>
          <div className="st-modal">
            <div className="st-prev st-prev-sm"><Thumb p={open} w={180} /></div>
            <div className="stack">
              <p className="muted st-nomargin">
                {STATE_ICON[open.state]?.[0]} {STATE_ICON[open.state]?.[1] ?? open.state}
                {open.scheduledAt && ` · ${fmtStamp(open.scheduledAt, tz)}`}{open.planTheme && ` · ${THEME_NAME[open.planTheme] ?? open.planTheme}`}{open.planDay > 0 && ` · day ${open.planDay}`}
              </p>
              {open.sub && <p className="st-nomargin">{open.sub}</p>}
              <p className="muted st-nomargin st-pre">{open.caption}</p>
              {open.error && <p className="st-bad st-nomargin">{open.error}</p>}
              {canEdit && (
                <div className="st-btns">
                  {open.state === 'posted' && <button className="primary" onClick={() => { setOpenId(''); onRepost(open); }}>Copy &amp; repost</button>}
                  {canAct(open) && <button className="primary" onClick={() => { setOpenId(''); onEdit(open); }}>Edit in composer</button>}
                  {canAct(open) && <button disabled={!!ops.busy} onClick={() => ops.rewrite(open)}>{ops.busy === open.id ? 'Rewriting…' : 'Rewrite'}</button>}
                  {canAct(open) && <button disabled={!!ops.busy} onClick={() => ops.postNow(open)}>Post now</button>}
                  {(open.state === 'draft' || open.state === 'scheduled') && <button disabled={!!ops.busy} onClick={() => ops.cancel(open)}>Remove from plan</button>}
                </div>
              )}
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
