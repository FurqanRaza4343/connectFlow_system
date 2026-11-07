import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { Modal } from '../components/Modal';
import { Asset, AssetKind, AssetList, KIND_LABEL, KindIcon, SUGGESTED_TAGS, Thumb, assetFile, fmtBytes, hostOf } from '../components/media';

export type MediaCfg = {
  firstMessage: { mode: 'off' | 'image' | 'link'; assetIds: string[] };
  followup1: { assetIds: string[] };
  followup2: { assetIds: string[] };
  replies: { enabled: boolean; allowTags: string[] };
};
export const DEFAULT_MEDIA: MediaCfg = { firstMessage: { mode: 'off', assetIds: [] }, followup1: { assetIds: [] }, followup2: { assetIds: [] }, replies: { enabled: false, allowTags: [] } };

const ids = (x: unknown): string[] => (Array.isArray(x) ? x.filter((i) => typeof i === 'string') : []);
/** Fill gaps so an older/partial server response never crashes the form. */
export function normMedia(m: any): MediaCfg {
  const mode = m?.firstMessage?.mode;
  return {
    firstMessage: { mode: mode === 'image' || mode === 'link' ? mode : 'off', assetIds: ids(m?.firstMessage?.assetIds) },
    followup1: { assetIds: ids(m?.followup1?.assetIds) },
    followup2: { assetIds: ids(m?.followup2?.assetIds) },
    replies: { enabled: !!m?.replies?.enabled, allowTags: ids(m?.replies?.allowTags) },
  };
}
/** A plain-language problem with the media settings, or ''. */
export function mediaProblem(m: MediaCfg): string {
  if (m.firstMessage.mode === 'image' && !m.firstMessage.assetIds.length) return 'Choose the picture for the first message, or set “First message” to Off.';
  if (m.firstMessage.mode === 'link' && !m.firstMessage.assetIds.length) return 'Choose the link for the first message, or set “First message” to Off.';
  if (m.replies.enabled && !m.replies.allowTags.length) return 'Pick at least one tag the AI may attach for, or turn “AI replies may attach files” off.';
  return '';
}

const MODES: Array<['off' | 'image' | 'link', string, string]> = [
  ['off', 'Text only', 'The first message is plain text, like today.'],
  ['image', 'Image with caption', 'One message: your picture, with the written message as its caption.'],
  ['link', 'Link only', 'One message: the written text plus your website link (WhatsApp shows a preview card).'],
];

/** Modal list of library files to pick from (single or multiple). */
function Picker({ title, assets, kinds, max, value, onClose, onDone }: { title: string; assets: Asset[]; kinds: AssetKind[]; max: number; value: string[]; onClose: () => void; onDone: (v: string[]) => void }) {
  const [sel, setSel] = useState<string[]>(value);
  const list = assets.filter((a) => kinds.includes(a.kind));
  const toggle = (id: string) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : max === 1 ? [id] : s.length < max ? [...s, id] : s));
  return (
    <Modal title={title} onClose={onClose} wide>
      {!list.length ? <p className="muted">No {kinds.map((k) => KIND_LABEL[k].toLowerCase()).join(' / ')} files in your library yet. Add some in the Assets tab first.</p> : (
        <div className="pick-grid" role={max === 1 ? 'radiogroup' : 'group'} aria-label={title}>
          {list.map((a) => {
            const on = sel.includes(a.id);
            return (
              <button key={a.id} type="button" className={`pick${on ? ' on' : ''}`} role={max === 1 ? 'radio' : 'checkbox'} aria-checked={on} onClick={() => toggle(a.id)}>
                <Thumb kind={a.kind} label={a.label} src={a.thumbUrl || (a.kind === 'image' ? assetFile(a.id) : null)} linkUrl={a.linkUrl} />
                <span className="pick-l">{a.label}</span>
                <small className="muted">{a.kind === 'link' ? hostOf(a.linkUrl) : fmtBytes(a.sizeBytes)}</small>
                {on && <span className="pick-tick" aria-hidden="true">✓</span>}
              </button>
            );
          })}
        </div>
      )}
      <div className="row between"><small className="muted">{max === 1 ? 'Choose one.' : `${sel.length} of ${max} selected`}</small>
        <span className="row"><button onClick={onClose}>Cancel</button><button className="primary" onClick={() => onDone(sel)}>Use selection</button></span></div>
    </Modal>
  );
}

/** The chosen files, as small removable thumbnails, plus the button that opens the picker. */
function Chosen({ label, hint, value, assets, kinds, max, disabled, why, onChange }: { label: string; hint?: string; value: string[]; assets: Asset[]; kinds: AssetKind[]; max: number; disabled: boolean; why: string; onChange: (v: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const by = new Map(assets.map((a) => [a.id, a]));
  return (
    <div className="chosen">
      <div className="row between"><b>{label}</b><button type="button" disabled={disabled} onClick={() => setOpen(true)} title={disabled ? why : ''}>{value.length ? 'Change…' : 'Choose…'}</button></div>
      {hint && <small className="muted">{hint}</small>}
      {disabled && why && <small className="muted">{why}</small>}
      {value.length > 0 && (
        <div className="att-strip">
          {value.map((id) => {
            const a = by.get(id);
            return a ? (
              <span key={id} className="att sel" title={a.label}><Thumb kind={a.kind} label={a.label} src={a.thumbUrl || (a.kind === 'image' ? assetFile(a.id) : null)} linkUrl={a.linkUrl} />
                <button type="button" className="att-x" aria-label={`Remove ${a.label}`} disabled={disabled} onClick={() => onChange(value.filter((x) => x !== id))}>✕</button></span>
            ) : null;
          })}
        </div>
      )}
      {open && <Picker title={label} assets={assets} kinds={kinds} max={max} value={value} onClose={() => setOpen(false)} onDone={(v) => { onChange(v); setOpen(false); }} />}
    </div>
  );
}

export function MediaSettings({ value, onChange, canEdit, go, followupsOn }: { value: MediaCfg; onChange: (m: MediaCfg) => void; canEdit: boolean; go?: (hash: string) => void; followupsOn: boolean }) {
  const [lib, setLib] = useState<AssetList | null>(null);
  const [err, setErr] = useState('');
  const [pruned, setPruned] = useState(false);
  const vref = useRef(value); vref.current = value;
  useEffect(() => { api<AssetList>('/api/assets').then(setLib).catch((e) => setErr(e.message)); }, []);

  // Files that were deleted since this setup was saved are dropped from the form (saving applies it).
  useEffect(() => {
    if (!lib) return;
    const have = new Set(lib.items.map((a) => a.id));
    const v = vref.current;
    const keep = (l: string[]) => l.filter((i) => have.has(i));
    const next: MediaCfg = { ...v, firstMessage: { ...v.firstMessage, assetIds: keep(v.firstMessage.assetIds) }, followup1: { assetIds: keep(v.followup1.assetIds) }, followup2: { assetIds: keep(v.followup2.assetIds) } };
    if (JSON.stringify(next) !== JSON.stringify(v)) { setPruned(true); onChange(next); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lib]);

  const assets = lib?.items ?? [];
  const empty = !!lib && !assets.length;
  const why = !lib ? 'Loading your files…' : empty ? 'Upload files in the Assets tab first.' : '';
  const set = (p: Partial<MediaCfg>) => onChange({ ...value, ...p });
  const libTags = Array.from(new Set(assets.flatMap((a) => a.tags)));
  const allTags = Array.from(new Set([...SUGGESTED_TAGS, ...libTags, ...value.replies.allowTags]));
  const tagCount = (t: string) => assets.filter((a) => a.tags.includes(t)).length;
  const fm = value.firstMessage;

  return (
    <div className="card stack"><b>Media</b>
      <p className="muted" style={{ margin: 0 }}>Let your messages carry your pictures, brochure and website link. Everything still waits for your approval, and dry-run still blocks sending.</p>
      {err && <div className="err" role="alert">{err}</div>}
      {empty && <div className="panel2 row between"><span>Your library is empty, so there is nothing to attach yet.</span>{go && <button type="button" className="primary" onClick={() => go('outreach/assets')}>Add files →</button>}</div>}
      {pruned && <div className="okbox">Some files you had chosen no longer exist and were removed here. Save to apply.</div>}

      <fieldset className="stack fs media-mode" disabled={!canEdit}>
        <legend className="muted">First message</legend>
        {MODES.map(([m, t, d]) => (
          <label key={m} className="radio-row">
            <input type="radio" name="fm-mode" checked={fm.mode === m} disabled={m !== 'off' && (empty || !lib)} onChange={() => set({ firstMessage: { mode: m, assetIds: m === fm.mode ? fm.assetIds : [] } })} />
            <span><b>{t}</b><small className="muted">{d}</small></span>
          </label>
        ))}
        {fm.mode !== 'off' && (
          <Chosen label={fm.mode === 'image' ? 'Picture' : 'Link'} hint={fm.mode === 'image' ? 'Your written message is sent as the caption.' : 'Add the link from your Assets.'} value={fm.assetIds} assets={assets}
            kinds={[fm.mode === 'image' ? 'image' : 'link']} max={1} disabled={!canEdit || empty || !lib} why={why} onChange={(v) => set({ firstMessage: { ...fm, assetIds: v } })} />
        )}
      </fieldset>

      <div className="stack">
        {!followupsOn && <small className="muted">Follow-ups are switched off above. These choices apply as soon as you turn them on.</small>}
        <Chosen label="With follow-up 1" hint="Optional, up to 3 files or a link." value={value.followup1.assetIds} assets={assets} kinds={['image', 'document', 'video', 'link']} max={3} disabled={!canEdit || empty || !lib} why={why} onChange={(v) => set({ followup1: { assetIds: v } })} />
        <Chosen label="With follow-up 2" hint="Optional, up to 3 files or a link." value={value.followup2.assetIds} assets={assets} kinds={['image', 'document', 'video', 'link']} max={3} disabled={!canEdit || empty || !lib} why={why} onChange={(v) => set({ followup2: { assetIds: v } })} />
      </div>

      <div className="stack">
        <label className="switch-row">
          <span><b>AI replies may attach files</b><small className="muted">When a lead asks “tell me about yourself” or about pricing, the AI can add a matching file or link from your library. It never invents links, and replies still wait for you unless auto-reply is on.</small></span>
          <input type="checkbox" role="switch" checked={value.replies.enabled} disabled={!canEdit || empty || !lib} onChange={(e) => set({ replies: { ...value.replies, enabled: e.target.checked } })} />
        </label>
        {value.replies.enabled && (
          <div className="stack">
            <small className="muted">Files with these tags may be attached:</small>
            <div className="att-strip" role="group" aria-label="Tags the AI may attach for">
              {allTags.map((t) => {
                const on = value.replies.allowTags.includes(t);
                return <button key={t} type="button" className={`tagchip${on ? ' on' : ''}`} aria-pressed={on} disabled={!canEdit} onClick={() => set({ replies: { ...value.replies, allowTags: on ? value.replies.allowTags.filter((x) => x !== t) : [...value.replies.allowTags, t] } })}>{t}<small>{tagCount(t)}</small></button>;
              })}
            </div>
            {!libTags.length && <small className="muted">None of your files has a tag yet. Add tags (like “services” or “pricing”) in the Assets tab so the AI knows which file fits.</small>}
          </div>
        )}
      </div>

      <div className="panel2 safe-note">
        <b><KindIcon kind="document" size={15} /> How we keep your number safe</b>
        <ul>
          <li>A new number sends <b>no media for its first 3 days</b>.</li>
          <li>While a number is warming up, at most about <b>30% of its messages</b> carry media.</li>
          <li>A first contact is <b>one message</b>, never a burst of pictures.</li>
          <li>Daily caps, active hours and STOP requests always apply.</li>
        </ul>
      </div>
    </div>
  );
}
