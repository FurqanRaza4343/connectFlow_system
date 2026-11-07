// Shared building blocks for the Assets library: types, thumbnails, attachment strips, the lightbox, the tag editor and the uploader.
import { useEffect, useRef, useState } from 'react';
import { errMessage } from '../api';
import { useDialog } from './Modal';

export type AssetKind = 'image' | 'document' | 'video' | 'link';
export type Asset = {
  id: string; kind: AssetKind; label: string; tags: string[]; mime: string; sizeBytes: number;
  previewUrl: string | null; thumbUrl: string | null; linkUrl: string | null; usedCount: number; createdAt: string;
  title?: string; variants?: Array<{ id: string; mime: string; sizeBytes: number; previewUrl: string }>;
};
export type AssetUsage = { bytes: number; limitBytes: number; count: number; limitCount: number; plan: string };
export type AssetList = { items: Asset[]; usage: AssetUsage };
export type Attachment = { assetId: string; kind: AssetKind; label: string; thumbUrl: string | null; linkUrl?: string | null };

export const KIND_LABEL: Record<AssetKind, string> = { image: 'Image', document: 'PDF', video: 'Video', link: 'Link' };
export const SUGGESTED_TAGS = ['services', 'pricing', 'portfolio', 'trust', 'booking'];
/** Per-file caps; the server is the source of truth and its message is always shown verbatim when it disagrees. */
export const MAX_BYTES: Record<Exclude<AssetKind, 'link'>, number> = { image: 5 * 1024 * 1024, document: 10 * 1024 * 1024, video: 16 * 1024 * 1024 };
export const ACCEPT = 'image/jpeg,image/png,image/webp,application/pdf,video/mp4';

export const assetFile = (id: string) => `/api/assets/${id}/file`;
export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
  if (n < 1073741824) return `${(n / 1048576).toFixed(n < 10485760 ? 1 : 0)} MB`;
  return `${(n / 1073741824).toFixed(1)} GB`;
}
export function hostOf(u: string | null | undefined): string {
  try { return new URL(u || '').hostname.replace(/^www\./, ''); } catch { return u || ''; }
}
export const cleanTag = (t: string) => t.trim().toLowerCase().replace(/[^a-z0-9 _-]/g, '').replace(/\s+/g, '-').slice(0, 24);

/** What kind of file is this, judged from its type/extension? null = not allowed. */
export function kindOfFile(f: File): Exclude<AssetKind, 'link'> | null {
  const t = f.type.toLowerCase(), n = f.name.toLowerCase();
  if (/^image\/(jpeg|png|webp)$/.test(t) || (!t && /\.(jpe?g|png|webp)$/.test(n))) return 'image';
  if (t === 'application/pdf' || (!t && n.endsWith('.pdf'))) return 'document';
  if (t === 'video/mp4' || (!t && n.endsWith('.mp4'))) return 'video';
  return null;
}
/** A friendly reason a file cannot be uploaded, or '' when it looks fine (checked before anything is sent). */
export function precheck(f: File): string {
  const k = kindOfFile(f);
  if (!k) return 'This file type is not supported. Use JPEG, PNG or WebP images, PDF documents or MP4 video.';
  if (f.size === 0) return 'This file is empty.';
  if (f.size > MAX_BYTES[k]) return `Too large: ${KIND_LABEL[k]} files can be up to ${fmtBytes(MAX_BYTES[k])} (this one is ${fmtBytes(f.size)}).`;
  return '';
}

const ICONS: Record<AssetKind, string> = {
  image: 'M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4M9 9.5a1 1 0 100-2 1 1 0 000 2z',
  document: 'M7 3h7l5 5v13H7zM14 3v5h5M9.5 13h6M9.5 17h6',
  video: 'M4 6h11v12H4zM15 10l5-3v10l-5-3',
  link: 'M10 14a4 4 0 005.7 0l2.8-2.8a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0L5.5 12.8a4 4 0 005.7 5.7l1-1',
};
export function KindIcon({ kind, size = 22 }: { kind: AssetKind; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICONS[kind]} /></svg>;
}

type ThumbData = { kind: AssetKind; label: string; src: string | null; linkUrl?: string | null };
/** A fixed-ratio preview: the picture when there is one, otherwise a tidy icon tile (no layout shift either way). */
export function Thumb({ kind, label, src, linkUrl }: ThumbData) {
  const [bad, setBad] = useState(false);
  const pic = !!src && !bad && (kind === 'image' || kind === 'video');
  return (
    <div className={`thumb kind-${kind}`}>
      {pic ? <img src={src!} alt={kind === 'image' ? label : ''} loading="lazy" onError={() => setBad(true)} /> : (
        <div className="thumb-tile"><KindIcon kind={kind} size={30} /><span>{kind === 'link' ? hostOf(linkUrl) || 'Link' : KIND_LABEL[kind]}</span></div>
      )}
      {pic && kind === 'video' && <span className="thumb-play" aria-hidden="true">▶</span>}
    </div>
  );
}

/** Full-size preview. Arrow keys move between files, Escape closes. */
export function Lightbox({ items, start, onClose }: { items: Array<{ id: string; kind: AssetKind; label: string }>; start: number; onClose: () => void }) {
  const [i, setI] = useState(start);
  const ref = useRef<HTMLDivElement>(null);
  useDialog(ref, onClose);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') setI((x) => Math.min(items.length - 1, x + 1));
      if (e.key === 'ArrowLeft') setI((x) => Math.max(0, x - 1));
    };
    addEventListener('keydown', h);
    return () => removeEventListener('keydown', h);
  }, [items.length]);
  const it = items[i];
  if (!it) return null;
  const url = assetFile(it.id);
  return (
    <div className="lb" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} tabIndex={-1} className="lb-box" role="dialog" aria-modal="true" aria-label={`Preview: ${it.label}`}>
        <div className="row between lb-head"><b className="lb-title">{it.label}</b><button className="ghost" onClick={onClose} aria-label="Close preview">✕</button></div>
        <div className="lb-body">
          {it.kind === 'image' && <img src={url} alt={it.label} />}
          {it.kind === 'video' && <video src={url} controls preload="metadata" />}
          {it.kind === 'document' && <div className="lb-file"><KindIcon kind="document" size={54} /><b>{it.label}</b><a className="btn-like" href={url} target="_blank" rel="noopener noreferrer">Open the PDF ↗</a></div>}
        </div>
        {items.length > 1 && (
          <div className="row between lb-foot">
            <button onClick={() => setI(i - 1)} disabled={i === 0} aria-label="Previous file">← Previous</button>
            <small className="muted">{i + 1} of {items.length}</small>
            <button onClick={() => setI(i + 1)} disabled={i === items.length - 1} aria-label="Next file">Next →</button>
          </div>
        )}
      </div>
    </div>
  );
}

/** Thumbnails of what a message carries. Pictures/PDFs/videos open a preview; a link shows as a link card. */
export function AttachmentStrip({ items, linkUrl, lead }: { items?: Attachment[] | null; linkUrl?: string | null; lead?: string }) {
  const [open, setOpen] = useState<number | null>(null);
  const files = (items || []).filter((a) => a.kind !== 'link');
  const links = (items || []).filter((a) => a.kind === 'link');
  if (!files.length && !links.length && !linkUrl) return null;
  return (
    <div className="att-wrap">
      {lead && <small className="muted">{lead}</small>}
      <div className="att-strip">
        {files.map((a, n) => (
          <button key={a.assetId} type="button" className="att" onClick={() => setOpen(n)} aria-label={`Preview ${KIND_LABEL[a.kind]}: ${a.label}`} title={a.label}>
            <Thumb kind={a.kind} label={a.label} src={a.thumbUrl || (a.kind === 'image' ? assetFile(a.assetId) : null)} />
          </button>
        ))}
        {linkUrl
          ? <a className="att-link" href={linkUrl} target="_blank" rel="noopener noreferrer" title={linkUrl}><KindIcon kind="link" size={18} /><span>{hostOf(linkUrl)}</span></a>
          : links.map((a) => (a.linkUrl
            ? <a key={a.assetId} className="att-link" href={a.linkUrl} target="_blank" rel="noopener noreferrer" title={a.linkUrl}><KindIcon kind="link" size={18} /><span>{a.label || hostOf(a.linkUrl)}</span></a>
            : <span key={a.assetId} className="att-link"><KindIcon kind="link" size={18} /><span>{a.label}</span></span>))}
      </div>
      {open !== null && <Lightbox items={files.map((a) => ({ id: a.assetId, kind: a.kind, label: a.label }))} start={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

/** Small "attached" chip for lists. */
export function MediaChip({ n, link, kind }: { n?: number; link?: boolean; kind?: string | null }) {
  if (!n && !link && !kind) return null;
  const text = n ? `${n} file${n > 1 ? 's' : ''}` : kind || 'link';
  return <span className="mtag" title="Media attached"><KindIcon kind={link && !n ? 'link' : 'image'} size={12} />{text}</span>;
}

/** Label for an inbound media message (the platform stores a placeholder like "[image]" instead of the file). */
export const MEDIA_WORD: Record<string, string> = { image: 'a photo', video: 'a video', document: 'a document', audio: 'an audio message', voice: 'a voice note', sticker: 'a sticker', location: 'a location', contact: 'a contact' };

/** Free-form tags with one-tap suggestions. */
export function TagEditor({ value, onChange, suggestions = SUGGESTED_TAGS, max = 8, id }: { value: string[]; onChange: (t: string[]) => void; suggestions?: string[]; max?: number; id?: string }) {
  const [txt, setTxt] = useState('');
  const add = (raw: string) => {
    const t = cleanTag(raw);
    if (t && !value.includes(t) && value.length < max) onChange([...value, t]);
    setTxt('');
  };
  const off = suggestions.filter((s) => !value.includes(s));
  return (
    <div className="tagedit">
      <div className="att-strip" aria-label="Selected tags">
        {value.map((t) => <span key={t} className="tagchip on">{t}<button type="button" onClick={() => onChange(value.filter((x) => x !== t))} aria-label={`Remove tag ${t}`}>✕</button></span>)}
        {value.length < max && (
          <input id={id} className="tagin" value={txt} maxLength={24} placeholder={value.length ? 'add tag' : 'e.g. services'} aria-label="Add a tag"
            onChange={(e) => (e.target.value.includes(',') ? add(e.target.value.replace(',', '')) : setTxt(e.target.value))}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(txt); } if (e.key === 'Backspace' && !txt && value.length) onChange(value.slice(0, -1)); }}
            onBlur={() => txt && add(txt)} />
        )}
      </div>
      {off.length > 0 && value.length < max && <div className="att-strip"><small className="muted">Quick add:</small>{off.map((s) => <button key={s} type="button" className="tagchip" aria-pressed="false" onClick={() => onChange([...value, s])}>+ {s}</button>)}</div>}
    </div>
  );
}

/** Upload with progress (fetch cannot report upload progress). */
export function uploadAsset(file: File, label: string, tags: string[], onProgress: (pct: number) => void): Promise<Asset> {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append('file', file); fd.append('label', label); fd.append('tags', tags.join(','));
    const x = new XMLHttpRequest();
    x.open('POST', '/api/assets');
    x.withCredentials = true;
    x.setRequestHeader('X-Requested-With', 'ConnectFlow');
    x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)); };
    x.onerror = () => reject(Object.assign(new Error('Network error. Check your connection and try again.'), { status: 0 }));
    x.ontimeout = x.onerror;
    x.onload = () => {
      let j: unknown = {};
      try { j = JSON.parse(x.responseText); } catch { /* not JSON */ }
      if (x.status >= 200 && x.status < 300) resolve(j as Asset);
      else reject(Object.assign(new Error(errMessage(j, x.status)), { status: x.status }));
    };
    x.send(fd);
  });
}
