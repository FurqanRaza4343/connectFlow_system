import { DragEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from 'react';
import { api, Me } from '../api';
import { Modal } from '../components/Modal';
import {
  ACCEPT, Asset, AssetKind, AssetList, AssetUsage, KIND_LABEL, KindIcon, Lightbox, TagEditor, Thumb,
  assetFile, fmtBytes, hostOf, precheck, uploadAsset,
} from '../components/media';

type Row = { key: string; name: string; size: number; pct: number; status: 'wait' | 'up' | 'done' | 'err'; error: string; code?: number };
const FILTERS: Array<['all' | AssetKind, string]> = [['all', 'All'], ['image', 'Images'], ['document', 'PDFs'], ['video', 'Videos'], ['link', 'Links']];
const labelOf = (name: string) => name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim().slice(0, 80) || 'Untitled';
const pctOf = (v: number, max: number) => (max > 0 ? Math.min(100, Math.round((v / max) * 100)) : 0);

function Meter({ label, text, pct }: { label: string; text: string; pct: number }) {
  return (
    <div className="asset-meter">
      <div className="row between"><span>{label}</span><span className="muted">{text}</span></div>
      <div className="meterbar" role="progressbar" aria-label={label} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${pct}%` }} className={pct >= 85 ? 'hot' : ''} /></div>
    </div>
  );
}

export function Assets({ me, go }: { me: Me; go: (hash: string) => void }) {
  const canEdit = ['owner', 'admin', 'operator'].includes(me.role);
  const [data, setData] = useState<AssetList | null>(null);
  const [err, setErr] = useState('');
  const [filter, setFilter] = useState<'all' | AssetKind>('all');
  const [rows, setRows] = useState<Row[]>([]);
  const [over, setOver] = useState(false);
  const [tags, setTags] = useState<string[]>([]);
  const [edit, setEdit] = useState<Asset | null>(null);
  const [del, setDel] = useState<Asset | null>(null);
  const [linkOpen, setLinkOpen] = useState(false);
  const [look, setLook] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const tagsRef = useRef(tags); tagsRef.current = tags;
  const usageRef = useRef<AssetUsage | null>(null); usageRef.current = data?.usage ?? null;
  const pending = useRef<Array<{ key: string; file: File }>>([]);
  const running = useRef(false);

  const load = useCallback(() => api<AssetList>('/api/assets').then((r) => { setData(r); setErr(''); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);

  const patch = (key: string, p: Partial<Row>) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...p } : r)));

  async function pump() {
    if (running.current) return;
    running.current = true;
    while (pending.current.length) {
      const t = pending.current.shift()!;
      patch(t.key, { status: 'up' });
      try {
        await uploadAsset(t.file, labelOf(t.file.name), tagsRef.current, (pct) => patch(t.key, { pct }));
        patch(t.key, { status: 'done', pct: 100 });
        setTimeout(() => setRows((rs) => rs.filter((r) => r.key !== t.key)), 3500);
        await load();
      } catch (e: any) {
        patch(t.key, { status: 'err', error: e.message, code: e.status });
        if ([402, 429, 507].includes(e.status)) { // the limit will not change for the next files: stop here instead of hammering the server
          for (const q of pending.current.splice(0)) patch(q.key, { status: 'err', error: `Skipped. ${e.message}`, code: e.status });
        }
      }
    }
    running.current = false;
  }

  function enqueue(list: FileList | File[]) {
    const files = Array.from(list);
    if (!files.length) return;
    const u = usageRef.current;
    let slots = u && u.limitCount ? Math.max(0, u.limitCount - u.count) : Infinity;
    const out: Row[] = [];
    for (const f of files) {
      const key = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      let bad = precheck(f);
      if (!bad && u && u.limitBytes && u.bytes >= u.limitBytes) bad = `Your ${u.plan} storage is full (${fmtBytes(u.limitBytes)}). Delete files you no longer use, or upgrade.`;
      if (!bad && slots <= 0) bad = `You have reached the limit of ${u?.limitCount} files on the ${u?.plan} plan. Delete one, or upgrade.`;
      if (bad) { out.push({ key, name: f.name, size: f.size, pct: 0, status: 'err', error: bad }); continue; }
      slots -= 1;
      out.push({ key, name: f.name, size: f.size, pct: 0, status: 'wait', error: '' });
      pending.current.push({ key, file: f });
    }
    setRows((rs) => [...rs, ...out]);
    pump();
  }

  const onDrop = (e: DragEvent) => { e.preventDefault(); setOver(false); if (canEdit) enqueue(e.dataTransfer.files); };
  const onZoneKey = (e: KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); file.current?.click(); } };

  const items = (data?.items ?? []).filter((a) => filter === 'all' || a.kind === filter);
  const u = data?.usage;
  const bytesPct = u ? pctOf(u.bytes, u.limitBytes) : 0, countPct = u ? pctOf(u.count, u.limitCount) : 0;
  const full = !!u && (bytesPct >= 100 || countPct >= 100);
  const near = !!u && !full && (bytesPct >= 85 || countPct >= 85);
  const lookItems = items.filter((a) => a.kind === 'image' || a.kind === 'video' || a.kind === 'document');
  const lookAt = look ? lookItems.findIndex((a) => a.id === look) : -1;

  return (
    <div className="stack">
      <p className="muted" style={{ margin: 0 }}>Upload the pictures, brochure and links your messages can carry. Pick which ones go out in <b>Setup → Media</b>; the AI only ever attaches files from this list.</p>
      {err && <div className="err" role="alert">{err} <button className="link" onClick={load}>Try again</button></div>}

      {u && (
        <div className="card stack">
          <div className="row between"><b>Storage on the {u.plan} plan</b>{(full || near) && <button className="primary" onClick={() => go('billing')}>{full ? 'Upgrade for more space' : 'See plans'}</button>}</div>
          <div className="two">
            <Meter label="Space used" text={`${fmtBytes(u.bytes)} of ${fmtBytes(u.limitBytes)}`} pct={bytesPct} />
            <Meter label="Files" text={`${u.count} of ${u.limitCount}`} pct={countPct} />
          </div>
          {full && <div className="err" role="status" style={{ margin: 0 }}>Your {u.plan} plan is full: new uploads are blocked until you delete something or upgrade.</div>}
          {near && <small className="muted">You have used more than 85% of your {u.plan} plan. Upgrading gives you more space.</small>}
        </div>
      )}

      {canEdit ? (
        <div className="card stack">
          <div
            className={`drop${over ? ' over' : ''}`} role="button" tabIndex={0} aria-label="Upload files: drop them here or press Enter to choose"
            onClick={() => file.current?.click()} onKeyDown={onZoneKey}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={onDrop}
          >
            <KindIcon kind="image" size={30} />
            <b>Drop files here, or click to choose</b>
            <small className="muted">JPEG, PNG or WebP images up to 5 MB · PDF up to 10 MB · MP4 video up to 16 MB</small>
          </div>
          <input ref={file} type="file" accept={ACCEPT} multiple hidden onChange={(e) => { if (e.target.files) enqueue(e.target.files); e.target.value = ''; }} />
          <div className="two assets-opts">
            <div><small className="muted">Tag new uploads (optional)</small><TagEditor value={tags} onChange={setTags} /></div>
            <div className="row" style={{ alignItems: 'flex-end', justifyContent: 'flex-end' }}><button onClick={() => setLinkOpen(true)}><KindIcon kind="link" size={16} /> Add a link</button></div>
          </div>
          {rows.length > 0 && (
            <div className="up-list" aria-live="polite">
              {rows.map((r) => (
                <div key={r.key} className={`up-row ${r.status}`}>
                  <div className="row between"><span className="up-name">{r.name}</span><small className="muted">{r.status === 'done' ? 'Uploaded ✓' : r.status === 'up' ? `${r.pct}%` : r.status === 'wait' ? 'Waiting…' : fmtBytes(r.size)}</small></div>
                  {(r.status === 'up' || r.status === 'wait' || r.status === 'done') && <div className="meterbar"><i style={{ width: `${r.status === 'wait' ? 0 : r.pct}%` }} /></div>}
                  {r.status === 'err' && (
                    <div className="row between"><small className="up-err">{r.error}</small>
                      <span className="row" style={{ gap: 6 }}>{(r.code === 402 || r.code === 507) && <button className="link" onClick={() => go('billing')}>Upgrade</button>}<button className="link" onClick={() => setRows((rs) => rs.filter((x) => x.key !== r.key))}>Dismiss</button></span></div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ) : <div className="card muted">You can view the library, but only owners, admins and operators can add or change files.</div>}

      <div className="chips" style={{ marginTop: 4 }} role="group" aria-label="Filter by type">
        {FILTERS.map(([k, l]) => <button key={k} className={`chipb${filter === k ? ' on' : ''}`} aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}{data && k !== 'all' ? <b>{data.items.filter((a) => a.kind === k).length}</b> : null}</button>)}
      </div>

      <div className="asset-grid">
        {!data && !err && [0, 1, 2, 3].map((i) => <div key={i} className="card asset-card sk"><div className="thumb skeleton" /><div className="asset-info"><div className="skeleton" style={{ height: 16, width: '70%' }} /><div className="skeleton" style={{ height: 12, width: '40%' }} /></div></div>)}
        {items.map((a) => (
          <div key={a.id} className="card asset-card">
            {a.kind === 'link'
              ? <a className="thumb-link" href={a.linkUrl || '#'} target="_blank" rel="noopener noreferrer" aria-label={`Open link ${a.label}`}><Thumb kind="link" label={a.label} src={null} linkUrl={a.linkUrl} /></a>
              : <button className="thumb-btn" onClick={() => setLook(a.id)} aria-label={`Preview ${KIND_LABEL[a.kind]}: ${a.label}`}><Thumb kind={a.kind} label={a.label} src={a.thumbUrl || (a.kind === 'image' ? assetFile(a.id) : null)} /></button>}
            <div className="asset-info">
              <b className="asset-label" title={a.label}>{a.label}</b>
              <small className="muted">{KIND_LABEL[a.kind]} · {a.kind === 'link' ? hostOf(a.linkUrl) : fmtBytes(a.sizeBytes)}{a.variants && a.variants.length > 0 ? ` · ${a.variants.length + 1} versions` : ''}</small>
              {a.tags.length > 0 && <div className="att-strip">{a.tags.map((t) => <span key={t} className="tagchip on static">{t}</span>)}</div>}
              <small className="muted">{a.usedCount > 0 ? `Used in ${a.usedCount} draft${a.usedCount > 1 ? 's' : ''}` : 'Not used yet'}</small>
              {canEdit && <div className="row" style={{ gap: 6, marginTop: 4 }}><button onClick={() => setEdit(a)}>Edit</button><button className="danger" onClick={() => setDel(a)}>Delete</button></div>}
            </div>
          </div>
        ))}
      </div>
      {data && !data.items.length && (
        <div className="card empty-assets">
          <KindIcon kind="image" size={34} />
          <b>Your library is empty</b>
          <p className="muted">Add what you would show a new customer: your logo, product or work photos, a brochure or price list (PDF), your website, a booking page. {canEdit ? 'Drop files above to start.' : 'Ask an owner or operator to add files.'}</p>
        </div>
      )}
      {data && data.items.length > 0 && !items.length && <p className="muted" style={{ textAlign: 'center' }}>Nothing of this type yet.</p>}

      {look && lookAt >= 0 && <Lightbox items={lookItems.map((a) => ({ id: a.id, kind: a.kind, label: a.label }))} start={lookAt} onClose={() => setLook(null)} />}
      {edit && <EditDialog asset={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
      {del && <DeleteDialog asset={del} onClose={() => setDel(null)} onDeleted={() => { setDel(null); load(); }} />}
      {linkOpen && <LinkDialog onClose={() => setLinkOpen(false)} defaultTags={tags} onSaved={() => { setLinkOpen(false); load(); }} />}
    </div>
  );
}

function EditDialog({ asset, onClose, onSaved }: { asset: Asset; onClose: () => void; onSaved: () => void }) {
  const [label, setLabel] = useState(asset.label);
  const [tags, setTags] = useState(asset.tags);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  return (
    <Modal title="Edit file" onClose={onClose}>
      <form className="stack" onSubmit={async (e) => {
        e.preventDefault(); setBusy(true); setErr('');
        try { await api(`/api/assets/${asset.id}`, 'PATCH', { label: label.trim(), tags }); onSaved(); } catch (x: any) { setErr(x.message); } finally { setBusy(false); }
      }}>
        <label>Name <small className="muted">shown to you only; it is not sent to customers</small><input value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)} required /></label>
        <div><small className="muted">Tags help the AI choose the right file for a question (e.g. “pricing” for a price list).</small><TagEditor value={tags} onChange={setTags} /></div>
        {err && <div className="err" role="alert">{err}</div>}
        <div className="row between"><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={busy || !label.trim()}>{busy ? 'Saving…' : 'Save'}</button></div>
      </form>
    </Modal>
  );
}

function DeleteDialog({ asset, onClose, onDeleted }: { asset: Asset; onClose: () => void; onDeleted: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  return (
    <Modal title="Delete this file?" onClose={onClose}>
      <p style={{ margin: 0 }}><b>{asset.label}</b> will be removed from your library and from Setup. Messages that were already sent are not affected.</p>
      {err && <div className="err" role="alert">{err}</div>}
      <div className="row between"><button onClick={onClose}>Keep it</button>
        <button className="primary danger-solid" disabled={busy} onClick={async () => {
          setBusy(true); setErr('');
          try { await api(`/api/assets/${asset.id}`, 'DELETE'); onDeleted(); } catch (x: any) { setErr(x.message); } finally { setBusy(false); }
        }}>{busy ? 'Deleting…' : 'Delete'}</button></div>
    </Modal>
  );
}

function LinkDialog({ onClose, onSaved, defaultTags }: { onClose: () => void; onSaved: () => void; defaultTags: string[] }) {
  const [url, setUrl] = useState('');
  const [label, setLabel] = useState('');
  const [tags, setTags] = useState(defaultTags);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const full = /^[a-z][a-z0-9+.-]*:\/\//i.test(url.trim()) ? url.trim() : url.trim() ? `https://${url.trim()}` : '';
  let valid = false;
  try { const x = new URL(full); valid = x.protocol === 'https:' && x.hostname.includes('.'); } catch { /* invalid */ }
  return (
    <Modal title="Add a link" onClose={onClose}>
      <form className="stack" onSubmit={async (e) => {
        e.preventDefault(); setBusy(true); setErr('');
        try { await api('/api/assets/link', 'POST', { url: full, label: label.trim(), tags }); onSaved(); } catch (x: any) { setErr(x.message); } finally { setBusy(false); }
      }}>
        <label>Web address<input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="yourcompany.com/book" inputMode="url" autoComplete="off" required /></label>
        {url && !valid && <small className="up-err">{/^http:\/\//i.test(url.trim()) ? 'Links must start with https://' : 'That does not look like a web address.'}</small>}
        <label>Name <small className="muted">optional; if empty we read the page title</small><input value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)} placeholder="Booking page" /></label>
        <div><small className="muted">Tags</small><TagEditor value={tags} onChange={setTags} /></div>
        {err && <div className="err" role="alert">{err}</div>}
        <div className="row between"><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={busy || !valid}>{busy ? 'Adding…' : 'Add link'}</button></div>
      </form>
    </Modal>
  );
}
