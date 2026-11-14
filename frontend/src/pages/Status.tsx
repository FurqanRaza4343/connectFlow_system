import { useCallback, useEffect, useRef, useState } from 'react';
import { api, Me } from '../api';
import '../status.css';
import { Brand } from '../components/status/Brand';
import { Composer } from '../components/status/Composer';
import { Planner } from '../components/status/Planner';
import { Posts, usePostOps } from '../components/status/Posts';
import { canEditRole, errText, Post, Seed, StatusData } from '../components/status/shared';

type StatusTab = 'create' | 'planner' | 'brand' | 'posts';
const TABS: Array<[StatusTab, string]> = [['create', 'Create'], ['planner', 'Planner'], ['brand', 'Brand kit & Autopilot'], ['posts', 'Posts']];

/** WhatsApp Status: create a post, plan a campaign, set the brand kit and autopilot, and see every post. */
export function Status({ me, go }: { me: Me; go: (hash: string) => void }) {
  const [data, setData] = useState<StatusData | null>(null);
  const [err, setErr] = useState('');
  const [tab, setTab] = useState<StatusTab>('create');
  const [seed, setSeed] = useState<Seed>(null);
  const [seedKey, setSeedKey] = useState(0);
  const [flash, setFlash] = useState<{ text: string; kind: 'ok' | 'bad' } | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout>>();
  const canEdit = canEditRole(me.role);
  const canProfile = ['owner', 'admin'].includes(me.role);

  const reload = useCallback(() => { api<StatusData>('/api/status').then((d) => { setData(d); setErr(''); }).catch((e) => setErr(errText(e))); }, []);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => () => clearTimeout(flashTimer.current), []);
  // The Posts tab refreshes itself every 15 s (a scheduled post turns "posted" while you look); the timer dies with the tab.
  useEffect(() => {
    if (tab !== 'posts') return;
    const t = setInterval(reload, 15000);
    return () => clearInterval(t);
  }, [tab, reload]);

  const notify = useCallback((text: string, kind: 'ok' | 'bad' = 'ok') => {
    setFlash({ text, kind });
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlash(null), kind === 'bad' ? 9000 : 4500);
  }, []);
  const ops = usePostOps(data?.capabilities ?? { renderer: true, aiImages: false, templates: [], plan: { postsPerDay: 1, autopilot: false, campaigns: false }, dryRun: true, timezone: 'UTC', numberReady: true, audience: 0, fake: false }, notify, reload);

  const load = (p: Post, repost: boolean) => { setSeed({ post: p, repost }); setSeedKey((k) => k + 1); setTab('create'); window.scrollTo({ top: 0, behavior: 'smooth' }); };
  const composerDone = () => { setSeed(null); setSeedKey((k) => k + 1); reload(); };

  if (!data) return <section><h2>WhatsApp Status</h2>{err ? <div className="err">{err} <button onClick={reload}>Try again</button></div> : <div className="skeleton" style={{ height: 160 }} />}</section>;
  const cap = data.capabilities;

  return (
    <section className="st">
      <h2>WhatsApp Status</h2>
      <div className="st-banners">
        {cap.dryRun && (
          <div className="st-note amber">
            <b>Dry run is ON.</b> Posts are only simulated and nothing reaches WhatsApp. This is the workspace's Outreach “dry run” switch (it also stops outreach messages): turn it off in <button type="button" className="link" onClick={() => go('outreach/setup')}>Outreach settings</button> when you are ready.
          </div>
        )}
        {!cap.numberReady && (
          <div className="st-note red">No WhatsApp number is connected, so nothing can be posted yet. <button type="button" className="link" onClick={() => go('numbers')}>Connect a number</button></div>
        )}
        {cap.numberReady && cap.audience === 0 && (
          <div className="st-note amber"><b>Nobody can see a Status yet.</b> A Status is only seen by your contacts: leads you contacted or who replied, plus the numbers in the audience list (Brand kit &amp; Autopilot tab). You have none yet.</div>
        )}
        {!cap.renderer && <div className="st-note amber">The picture renderer is not available right now, so posts go out as <b>text statuses</b>.</div>}
        <div className="st-note">
          <b>Who sees a Status?</b> Only people who have your WhatsApp number saved in their contacts ({cap.audience} right now). It stays for 24 hours. WhatsApp does not tell us who viewed it: look at <b>Status → My status</b> on the phone. Keep to 1 to 3 posts a day.
          <small className="muted"> Your plan: up to {cap.plan.postsPerDay} posts a day · Autopilot {cap.plan.autopilot ? 'included' : 'not included'} · Campaigns {cap.plan.campaigns ? 'included' : 'not included'}.</small>
        </div>
      </div>
      {flash && <div className={`st-toast ${flash.kind}`} role="status" onClick={() => setFlash(null)}>{flash.text}</div>}

      <div className="subtabs" role="tablist">
        {TABS.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}</button>)}
      </div>

      {tab === 'create' && <Composer key={seedKey} data={data} canEdit={canEdit} seed={seed} notify={notify} onDone={composerDone} />}
      {tab === 'planner' && <Planner data={data} canEdit={canEdit} notify={notify} reloadData={reload} onEdit={(p) => load(p, false)} onRepost={(p) => load(p, true)} />}
      {tab === 'brand' && <Brand key={JSON.stringify(data.profile)} data={data} canSave={canProfile} notify={notify} onSaved={reload} goBilling={() => go('billing')} />}
      {tab === 'posts' && <Posts posts={data.posts} cap={cap} canEdit={canEdit} ops={ops} onEdit={(p) => load(p, false)} onRepost={(p) => load(p, true)} />}
    </section>
  );
}
