import { FormEvent, useState } from 'react';
import { api } from '../../api';
import { Colours } from './Composer';
import { errText, linkMode, Notify, PALETTES, Profile, StatusData } from './shared';

const DEF = { accent: '#004ecb', dark: '#0b1b3a', ink: '#ffffff' };
const LANGS = ['English', 'Roman Urdu', 'Urdu'];

/** Brand kit + autopilot + offers. Only owners/admins can save (the API says so); everybody else sees it read-only. */
export function Brand({ data, canSave, notify, onSaved, goBilling }: { data: StatusData; canSave: boolean; notify: Notify; onSaved: () => void; goBilling: () => void }) {
  const p = data.profile, cap = data.capabilities, u = data.usage;
  const maxPerDay = Math.max(1, Math.min(5, cap.plan.postsPerDay));

  const [col, setCol] = useState({ custom: !!(p.accent || p.dark || p.ink), accent: p.accent || DEF.accent, dark: p.dark || DEF.dark, ink: p.ink || DEF.ink });
  const [bt, setBt] = useState(p.button_text);
  const [lm, setLm] = useState(linkMode(p.button_link));
  const [lu, setLu] = useState(linkMode(p.button_link) === 'url' ? p.button_link : '');
  const [waNum, setWaNum] = useState(p.wa_number);
  const [waText, setWaText] = useState(p.wa_text);
  const [footer, setFooter] = useState(p.footer);
  const [tpl, setTpl] = useState<Set<string>>(() => new Set(p.templates.trim() ? p.templates.split(',').map((s) => s.trim()) : cap.templates));

  const [auto, setAuto] = useState(p.auto && cap.plan.autopilot);
  const [times, setTimes] = useState(p.times);
  const [perDay, setPerDay] = useState(Math.min(p.per_day, maxPerDay));
  const [approval, setApproval] = useState(p.approval || 'review');
  const [imgStyle, setImgStyle] = useState(cap.aiImages ? p.image_style : 'card');
  const [dayCap, setDayCap] = useState(p.image_daily_cap);
  const [monthCap, setMonthCap] = useState(p.image_monthly_cap);
  const [lang, setLang] = useState(p.language || 'English');
  const [recipients, setRecipients] = useState(p.recipients);
  const [offers, setOffers] = useState(p.offers);
  const [busy, setBusy] = useState('');

  async function save(part: string, patch: Partial<Profile>, done: string) {
    setBusy(part);
    try { await api('/api/status/profile', 'PUT', patch); notify(done, 'ok'); onSaved(); } catch (e) { notify(errText(e), 'bad'); } finally { setBusy(''); }
  }
  const saveBrand = (e: FormEvent) => {
    e.preventDefault();
    void save('brand', {
      accent: col.custom ? col.accent : '', dark: col.custom ? col.dark : '', ink: col.custom ? col.ink : '', button_text: bt, button_link: lm === 'wa' ? 'wa' : lm === 'url' ? lu.trim() : '',
      wa_number: waNum, wa_text: waText, footer, templates: data.capabilities.templates.filter((t) => tpl.has(t)).join(','),
    }, 'Brand kit saved');
  };
  const saveAuto = (e: FormEvent) => {
    e.preventDefault();
    void save('auto', { auto, times, per_day: perDay, approval, image_style: imgStyle, image_daily_cap: dayCap, image_monthly_cap: monthCap, language: lang, recipients }, 'Autopilot saved');
  };
  const palette = (i: number) => setCol({ custom: true, accent: PALETTES[i][1], dark: PALETTES[i][2], ink: '#ffffff' });
  const toggleTpl = (t: string, on: boolean) => setTpl((s) => { const n = new Set(s); if (on) n.add(t); else n.delete(t); return n; });

  return (
    <>
      {!canSave && <p className="muted st-small">Only the workspace owner or an admin can change the brand kit and autopilot. You can see the current settings.</p>}
      <form className="card stack" onSubmit={saveBrand}>
        <h2>Brand kit</h2>
        <p className="muted st-small">One look for every post: the Create tab, the autopilot and campaigns all read this. Your logo comes from Settings → Appearance.</p>
        <fieldset className="fs st-fs" disabled={!canSave}>
          <div className="stack">
            <Colours v={col} onChange={(x) => setCol((c) => ({ ...c, ...x }))} onPalette={palette} disabled={!canSave} />
            <div className="st-cols">
              <label>Button text<input maxLength={40} value={bt} onChange={(e) => setBt(e.target.value)} /></label>
              <label>Default button link
                <select value={lm} onChange={(e) => setLm(e.target.value as typeof lm)}><option value="none">None</option><option value="wa">Chat on WhatsApp</option><option value="url">My own link</option></select>
              </label>
              {lm === 'url' && <label>Link<input maxLength={300} placeholder="https://…" value={lu} onChange={(e) => setLu(e.target.value)} /></label>}
            </div>
            <p className="muted st-small">A Status picture has no tappable area, so the button link is written into the caption (“👉 button text: link”).</p>
            <div className="st-cols">
              <label>WhatsApp number for “Chat on WhatsApp”<input value={waNum} onChange={(e) => setWaNum(e.target.value)} placeholder="empty = the connected number" /></label>
              <label>Opening message<input maxLength={120} value={waText} onChange={(e) => setWaText(e.target.value)} /></label>
            </div>
            <label>Bottom line on every picture<input maxLength={160} placeholder="yourwebsite.com · @yourhandle" value={footer} onChange={(e) => setFooter(e.target.value)} /></label>
            <div>
              <b className="st-small">Designs the autopilot may use</b>
              <div className="st-chips st-gap">{cap.templates.map((t) => <label key={t} className="chk"><input type="checkbox" checked={tpl.has(t)} onChange={(e) => toggleTpl(t, e.target.checked)} />{t}</label>)}</div>
            </div>
            <div className="row end"><button className="primary" type="submit" disabled={busy === 'brand'}>Save brand kit</button></div>
          </div>
        </fieldset>
      </form>

      <form className="card stack st-sect" onSubmit={saveAuto}>
        <h2>Autopilot</h2>
        {!cap.plan.autopilot && (
          <div className="st-note amber">Autopilot is not part of your current plan. <button type="button" className="link" onClick={goBilling}>See plans in Billing</button></div>
        )}
        <fieldset className="fs st-fs" disabled={!canSave}>
          <div className="stack">
            <label className="chk"><input type="checkbox" checked={auto} disabled={!cap.plan.autopilot} onChange={(e) => setAuto(e.target.checked)} />Make Status posts automatically <small className="muted">— at the times below, from your business data and offers, with the brand kit above</small></label>
            <div className="st-cols">
              <label>Posting times ({cap.timezone})<input value={times} onChange={(e) => setTimes(e.target.value)} placeholder="11:00,19:00" /></label>
              <label>Posts per day (1 to {maxPerDay})<input type="number" min={1} max={maxPerDay} value={perDay} onChange={(e) => setPerDay(Math.max(1, Math.min(maxPerDay, Number(e.target.value) || 1)))} />
                <small className="muted">Your plan allows up to {cap.plan.postsPerDay} a day.</small></label>
              <label>When a post is ready<select value={approval} onChange={(e) => setApproval(e.target.value)}><option value="review">Wait for my approval (recommended)</option><option value="auto">Post by itself</option></select></label>
            </div>
            <div className="st-cols">
              <label>Picture style
                <select value={imgStyle} onChange={(e) => setImgStyle(e.target.value)}>
                  <option value="card">Designed cards (free)</option>
                  <option value="ai" disabled={!cap.aiImages}>AI pictures{cap.aiImages ? '' : ': AI pictures are not set up yet'}</option>
                  <option value="mixed" disabled={!cap.aiImages}>AI pictures when allowed{cap.aiImages ? '' : ': not set up yet'}</option>
                </select>
              </label>
              <label>AI pictures per day<input type="number" min={0} value={dayCap} onChange={(e) => setDayCap(Number(e.target.value) || 0)} /></label>
              <label>AI pictures per month<input type="number" min={0} value={monthCap} onChange={(e) => setMonthCap(Number(e.target.value) || 0)} /></label>
              <label>Language<select value={lang} onChange={(e) => setLang(e.target.value)}>{LANGS.map((l) => <option key={l}>{l}</option>)}</select></label>
            </div>
            <label>Audience list (optional)<input value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="phone numbers, comma separated" />
              <small className="muted">A Status is only seen by contacts: leads you contacted or who replied, plus the numbers in this list. Currently {cap.audience} contact{cap.audience === 1 ? '' : 's'}.</small></label>
            <div className="row end"><button className="primary" type="submit" disabled={busy === 'auto'}>Save autopilot</button></div>
          </div>
        </fieldset>
      </form>

      <div className="card stack st-sect">
        <h2>Current offers</h2>
        <fieldset className="fs st-fs" disabled={!canSave}>
          <label>Current offers (the ONLY discounts the AI may use)<textarea rows={3} value={offers} onChange={(e) => setOffers(e.target.value)} placeholder="e.g. 30% off AI chatbot setup until 30 November" /></label>
          <div className="row end st-gap"><button type="button" disabled={busy === 'offers'} onClick={() => save('offers', { offers }, 'Offers saved')}>Save offers</button></div>
        </fieldset>
        <p className="muted st-small">
          Used so far: today {u.today.copies} idea(s), {u.today.images} AI picture(s), {u.today.posted} posted · this month {u.month.copies} idea(s), {u.month.images} AI picture(s), {u.month.posted} posted
          {u.month.costUsd > 0 ? ` · about $${u.month.costUsd.toFixed(3)} on pictures` : ''}. {cap.aiImages ? '' : 'AI pictures are not set up yet.'}
        </p>
      </div>
    </>
  );
}
