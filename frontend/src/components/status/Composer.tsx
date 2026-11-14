import { useRef, useState } from 'react';
import { api } from '../../api';
import { Idea, IDEA_WORDS, imageUrl, linkMode, Notify, PALETTES, Post, PRESETS, Seed, StatusData, errText, useElapsed } from './shared';

type Style = 'card' | 'ai' | 'own';
type Form = {
  headline: string; sub: string; badge: string; cta: string; caption: string; link: string; template: string; style: Style;
  linkMode: 'none' | 'wa' | 'url'; linkUrl: string; overlay: number; pos: string; plain: boolean; custom: boolean; accent: string; dark: string; ink: string;
};
type Mode = 'ai' | 'prompt' | 'own';

const DEF = { accent: '#004ecb', dark: '#0b1b3a', ink: '#ffffff' };

function formFrom(p: Post | null): Form {
  const custom = !!(p && (p.accent || p.dark || p.ink));
  return {
    headline: p?.headline ?? '', sub: p?.sub ?? '', badge: p?.badge ?? '', cta: p?.cta ?? '', caption: p?.caption ?? '', link: p?.link ?? '', template: p?.template || 'gradient',
    style: p?.bgUpload ? 'own' : 'card', linkMode: linkMode(p?.btnLink ?? ''), linkUrl: p && linkMode(p.btnLink) === 'url' ? p.btnLink : '',
    overlay: p?.overlay ?? 48, pos: p?.pos || 'center', plain: p?.plain ?? false, custom, accent: p?.accent || DEF.accent, dark: p?.dark || DEF.dark, ink: p?.ink || DEF.ink,
  };
}

/** Create tab: three entry modes, the form, and the live 9:16 preview. Remounted (key) whenever a post is loaded into it. */
export function Composer({ data, canEdit, seed, notify, onDone }: { data: StatusData; canEdit: boolean; seed: Seed; notify: Notify; onDone: () => void }) {
  const cap = data.capabilities;
  const post = seed?.post ?? null;
  const editId = seed && !seed.repost ? seed.post.id : null;
  const replaceId = seed?.repost ? seed.post.id : null;

  const [mode, setMode] = useState<Mode>(post && post.mode === 'prompt' ? 'prompt' : post ? 'own' : 'ai');
  const [f, setF] = useState<Form>(() => formFrom(post));
  const [prompt, setPrompt] = useState(post?.prompt ?? '');
  const [imagePrompt, setImagePrompt] = useState(post?.imagePrompt ?? '');
  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [ideasSince, setIdeasSince] = useState<number | null>(null);
  const [bgKey, setBgKey] = useState('');
  const [fileName, setFileName] = useState('');
  const [preview, setPreview] = useState(post?.hasImage ? imageUrl(post.id, String(Date.now())) : '');
  const [previewBusy, setPreviewBusy] = useState(false);
  const [busy, setBusy] = useState('');
  const [when, setWhen] = useState('');
  const [deleteOld, setDeleteOld] = useState(true);
  const file = useRef<HTMLInputElement>(null);
  const sec = useElapsed(ideasSince);

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((x) => ({ ...x, [k]: v }));
  const ownPicture = !!bgKey || !!post?.bgUpload;
  const style: Style = f.style === 'own' && !ownPicture ? 'card' : f.style;
  const showPic = style === 'own' || ownPicture;

  const body = (x: Form = f) => {
    const st: Style = x.style === 'own' && !ownPicture ? 'card' : x.style;
    return {
      headline: x.headline, sub: x.sub, badge: x.badge, cta: x.cta, caption: x.caption, link: x.link, template: x.template, style: st, kind: 'image',
      mode: mode === 'prompt' ? 'prompt' : mode === 'ai' ? 'ai' : 'own', prompt, image_prompt: imagePrompt, bg_upload: st === 'own' ? bgKey : '',
      btn_link: x.linkMode === 'wa' ? 'wa' : x.linkMode === 'url' ? x.linkUrl.trim() : '',
      overlay: Number(x.overlay), pos: x.pos, plain: x.plain, accent: x.custom ? x.accent : '', dark: x.custom ? x.dark : '', ink: x.custom ? x.ink : '',
    };
  };
  const hasContent = (x: Form = f) => !!x.headline.trim() || (x.plain && ownPicture);

  async function runPreview(x: Form = f) {
    if (!hasContent(x)) { notify('Write a headline first', 'bad'); return; }
    setPreviewBusy(true);
    try { const r = await api<{ png: string }>('/api/status/preview', 'POST', body(x)); setPreview(r.png); } catch (e) { notify(errText(e), 'bad'); } finally { setPreviewBusy(false); }
  }

  async function suggest() {
    if (mode === 'prompt' && !prompt.trim()) { notify('Write what the post should say', 'bad'); return; }
    setIdeasSince(Date.now());
    try { const r = await api<{ ideas: Idea[] }>('/api/status/ideas', 'POST', { prompt: mode === 'prompt' ? prompt.trim() : '', count: 3 }); setIdeas(r.ideas); } catch (e) { notify(errText(e), 'bad'); } finally { setIdeasSince(null); }
  }

  function applyIdea(i: Idea) {
    const next: Form = { ...f, headline: i.headline, sub: i.sub, badge: i.badge, cta: i.cta, caption: i.caption, template: i.badge ? 'badge' : 'gradient' };
    setF(next); setImagePrompt(i.imagePrompt || ''); setIdeas([]); setPreview('');
    void runPreview(next);
  }

  function palette(i: number) {
    const [, accent, dark] = PALETTES[i];
    const next = { ...f, accent, dark, ink: '#ffffff', custom: true };
    setF(next);
    if (hasContent(next)) void runPreview(next);
  }

  function pick(file0: File | undefined) {
    if (!file0) return;
    if (file0.size > 3 * 1024 * 1024) { notify('The picture is too large (max 3 MB)', 'bad'); return; }
    const rd = new FileReader();
    rd.onload = async () => {
      try {
        const r = await api<{ file: string }>('/api/status/upload', 'POST', { data_url: String(rd.result) });
        setBgKey(r.file); setFileName(file0.name); setF((x) => ({ ...x, style: 'own' })); notify('Picture attached: press Preview', 'ok');
      } catch (e) { notify(errText(e), 'bad'); }
    };
    rd.readAsDataURL(file0);
  }

  async function save(action: 'draft' | 'schedule' | 'post') {
    if (!hasContent()) { notify('Write a headline first', 'bad'); return; }
    let iso = '';
    if (action === 'schedule') {
      if (!when) { notify('Choose a date and time to schedule', 'bad'); return; }
      iso = new Date(when).toISOString();
    }
    if (action === 'post' && !confirm(`Post this Status now?\n\nIt becomes visible to your WhatsApp contacts for 24 hours.${cap.dryRun ? '\n\nDry run is ON: nothing will actually be sent.' : ''}`)) return;
    setBusy(action);
    try {
      const b = body();
      let dry = cap.dryRun;
      if (editId && action === 'draft') await api(`/api/status/posts/${editId}`, 'PUT', { ...b, new_background: b.style === 'ai' });
      else if (editId) {
        await api(`/api/status/posts/${editId}`, 'PUT', { ...b, new_background: false });
        if (action === 'post') { const r = await api<{ dryRun?: boolean }>(`/api/status/posts/${editId}/post`, 'POST'); dry = !!r.dryRun || dry; }
        else await api(`/api/status/posts/${editId}/schedule`, 'POST', { scheduled_at: iso });
      } else {
        const r = await api<{ post?: Post; result?: { dryRun?: boolean }; dryRun?: boolean }>('/api/status/posts', 'POST', { ...b, action, scheduled_at: iso || undefined });
        dry = !!(r.result?.dryRun ?? r.dryRun) || dry;
      }
      notify(action === 'post' && dry ? 'Dry run: saved, nothing was sent to WhatsApp' : action === 'post' ? 'Posted to your WhatsApp Status' : action === 'schedule' ? 'Scheduled' : 'Saved', 'ok');
      if (replaceId && action === 'post' && !dry && deleteOld) {
        try { await api(`/api/status/posts/${replaceId}/delete-wa`, 'POST'); notify('Posted the new one and deleted the old Status from WhatsApp', 'ok'); }
        catch (e) { notify(`The new Status is live, but the old one could not be deleted (${errText(e)}). Delete it from the Posts tab or on the phone.`, 'bad'); }
      }
      onDone();
    } catch (e) { notify(errText(e), 'bad'); } finally { setBusy(''); }
  }

  const ideaLabel = ideasSince === null ? null : `${IDEA_WORDS[Math.floor(sec / 4) % IDEA_WORDS.length]} ${sec}s`;
  const modeBtn = (m: Mode, label: string) => <button type="button" className={`chipb${mode === m ? ' on' : ''}`} onClick={() => { setMode(m); setIdeas([]); }}>{label}</button>;

  return (
    <>
      {replaceId && (
        <div className="st-note amber">
          <b>You are editing a copy of a Status that is already live.</b> WhatsApp cannot change a posted Status, so the edited version is posted as a NEW one (its viewers start from zero).
          <label className="chk st-gap"><input type="checkbox" checked={deleteOld} onChange={(e) => setDeleteOld(e.target.checked)} />Delete the old live Status when I press <b>Post now</b></label>
          {post?.bgUpload && <small className="muted">The copy does not carry the uploaded background picture: upload it again if you want it.</small>}
        </div>
      )}
      <div className="st-grid">
        <div className="card">
          <h2>{editId ? 'Edit this post' : 'Create a Status'}</h2>
          {!canEdit && <p className="muted st-small">You have read-only access: you can look around, but only an operator, admin or owner can create posts.</p>}
          <fieldset className="fs st-fs" disabled={!canEdit}>
            <div className="st-chips">{modeBtn('ai', '✦ From my business data')}{modeBtn('prompt', '✎ My prompt')}{modeBtn('own', '▣ My own text / picture')}</div>
            {mode === 'ai' && (
              <div className="stack">
                <p className="muted st-small">The AI reads your company data and your current offers (Brand kit tab) and suggests ideas. It never invents a discount.</p>
                <div><button type="button" disabled={ideasSince !== null} onClick={suggest}>{ideaLabel ?? 'Suggest 3 ideas'}</button></div>
              </div>
            )}
            {mode === 'prompt' && (
              <div className="stack">
                <label>What should the post say?<textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} maxLength={800} placeholder="e.g. Make a 30% off offer for our AI chatbot service, valid this week" /></label>
                <div className="st-chips">{PRESETS.map(([n, t]) => <button key={n} type="button" className="chipb" onClick={() => setPrompt(t)}>{n}</button>)}</div>
                <div><button type="button" disabled={ideasSince !== null} onClick={suggest}>{ideaLabel ?? 'Generate from my prompt'}</button></div>
              </div>
            )}
            {ideas.length > 0 && (
              <div className="stack st-ideas">
                {ideas.map((x, i) => <button type="button" key={i} className="st-idea" onClick={() => applyIdea(x)}><b>{x.headline}</b><small className="muted">{x.sub || x.caption}</small></button>)}
              </div>
            )}
            <div className="stack st-form">
              <label>Headline<input maxLength={120} placeholder="Short and clear (max ~60 characters)" value={f.headline} onChange={(e) => set('headline', e.target.value)} /></label>
              <label>Second line (optional)<input maxLength={160} value={f.sub} onChange={(e) => set('sub', e.target.value)} /></label>
              <div className="st-cols">
                <label>Offer badge (optional)<input maxLength={14} placeholder="30% OFF" value={f.badge} onChange={(e) => set('badge', e.target.value)} /></label>
                <label>Button text<input maxLength={40} placeholder="Message us today" value={f.cta} onChange={(e) => set('cta', e.target.value)} /></label>
              </div>
              <div className="st-cols">
                <label>Button link
                  <select value={f.linkMode} onChange={(e) => set('linkMode', e.target.value as Form['linkMode'])}>
                    <option value="none">No link</option><option value="wa">Chat on WhatsApp</option><option value="url">My own link (website, Instagram, Facebook…)</option>
                  </select>
                  <small className="muted">A Status picture cannot be tapped, so the link is added to the <b>caption</b> as “👉 button text: link”.</small>
                </label>
                {f.linkMode === 'url' && <label>Link<input maxLength={300} placeholder="https://instagram.com/yourpage" value={f.linkUrl} onChange={(e) => set('linkUrl', e.target.value)} /></label>}
              </div>
              <label>Caption under the picture<textarea rows={3} maxLength={900} placeholder="A short description. Max 1024 characters with the link." value={f.caption} onChange={(e) => set('caption', e.target.value)} /></label>
              <label>Contact line (shown on the picture and in the caption)<input maxLength={200} placeholder="yourwebsite.com · instagram.com/yourpage" value={f.link} onChange={(e) => set('link', e.target.value)} /></label>
              <div className="st-cols">
                <label>Design<select value={f.template} onChange={(e) => set('template', e.target.value)}>{cap.templates.map((t) => <option key={t} value={t}>{t}</option>)}</select></label>
                <label>Background
                  <select value={style} onChange={(e) => set('style', e.target.value as Style)}>
                    <option value="card">Designed card (free)</option>
                    <option value="ai" disabled={!cap.aiImages}>{cap.aiImages ? 'AI picture (uses your image quota)' : 'AI pictures are not set up yet'}</option>
                    <option value="own">My own picture</option>
                  </select>
                </label>
              </div>
              <div className="row">
                <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ''; }} />
                <button type="button" onClick={() => file.current?.click()}>Upload my picture</button>
                <small className="muted">{ownPicture ? `${fileName || 'picture'} attached` : 'PNG, JPEG or WebP, max 3 MB'}</small>
              </div>
              {showPic && (
                <div className="st-cols">
                  <label>Darken the picture: <b>{f.overlay}</b>%<input type="range" min={0} max={85} value={f.overlay} onChange={(e) => set('overlay', Number(e.target.value))} /></label>
                  <label>Text position<select value={f.pos} onChange={(e) => set('pos', e.target.value)}><option value="top">top</option><option value="center">center</option><option value="bottom">bottom</option></select></label>
                  <label className="chk"><input type="checkbox" checked={f.plain} onChange={(e) => set('plain', e.target.checked)} />Picture only (no text on it)</label>
                </div>
              )}
              <Colours v={f} onChange={(p) => setF((x) => ({ ...x, ...p }))} onPalette={palette} />
              <div className="st-actions">
                <button type="button" disabled={previewBusy} onClick={() => runPreview()}>{previewBusy ? 'Drawing…' : 'Preview'}</button>
                <button type="button" disabled={!!busy} onClick={() => save('draft')}>{editId ? 'Save changes' : 'Save draft'}</button>
                <label className="st-when">Schedule for<input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></label>
                <button type="button" disabled={!!busy} onClick={() => save('schedule')}>Schedule</button>
                <button type="button" className="primary" disabled={!!busy} onClick={() => save('post')}>{busy === 'post' ? 'Posting…' : 'Post now'}</button>
              </div>
              {editId && <small className="muted">Editing post {editId.slice(0, 8)}. <button type="button" className="link" onClick={onDone}>Start a new one</button></small>}
            </div>
          </fieldset>
        </div>
        <div className="card st-prevcard">
          <h2>Preview</h2>
          <div className="st-prev">
            {preview ? <img src={preview} alt="Status preview" /> : <div className="muted st-prev-empty">Fill in the headline and press <b>Preview</b>.<br />Your logo and colours (Brand kit tab) are used.</div>}
          </div>
        </div>
      </div>
    </>
  );
}

/** Custom colours + palette chips, shared by the composer and the brand kit. */
export function Colours({ v, onChange, onPalette, disabled }: {
  v: { custom: boolean; accent: string; dark: string; ink: string }; onChange: (p: Partial<{ custom: boolean; accent: string; dark: string; ink: string }>) => void; onPalette: (i: number) => void; disabled?: boolean;
}) {
  return (
    <div className="st-colours">
      <label className="chk"><input type="checkbox" disabled={disabled} checked={v.custom} onChange={(e) => onChange({ custom: e.target.checked })} />Custom colours <small className="muted">— otherwise the brand kit colours are used</small></label>
      <div className="st-pickrow">
        <label className="chk">Main <input type="color" disabled={disabled} value={v.accent} onChange={(e) => onChange({ accent: e.target.value, custom: true })} /></label>
        <label className="chk">Dark <input type="color" disabled={disabled} value={v.dark} onChange={(e) => onChange({ dark: e.target.value, custom: true })} /></label>
        <label className="chk">Text <input type="color" disabled={disabled} value={v.ink} onChange={(e) => onChange({ ink: e.target.value, custom: true })} /></label>
      </div>
      <div className="st-chips">
        {PALETTES.map(([n, a], i) => <button key={n} type="button" className="chipb" disabled={disabled} title={n} onClick={() => onPalette(i)}><span className="st-dot" style={{ background: a }} />{n}</button>)}
      </div>
      <small className="muted">The text colour is chosen automatically unless you set Custom colours.</small>
    </div>
  );
}
