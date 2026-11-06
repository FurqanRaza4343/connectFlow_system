import { useRef, useState } from 'react';
import { api, Me } from '../api';
import { DataCard, SecurityCard } from '../components/AccountSecurity';
import { applyTheme, DEFAULT_BRAND, fileToDataUrl, PRESETS, Theme, THEMES } from '../theme';

const same = (a: Theme, b: Theme) => a.accent === b.accent && a.sidebar === b.sidebar && a.page === b.page;

export function Settings({ me, onChange }: { me: Me; onChange: () => void }) {
  const t = me.tenant!;
  const saved: Theme = { accent: t.brand.color, sidebar: t.brand.sidebar, page: t.brand.page };
  const [name, setName] = useState(t.name);
  const [site, setSite] = useState(t.website || '');
  const [theme, setTheme] = useState<Theme>(saved);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const file = useRef<HTMLInputElement>(null);

  const run = async (fn: () => Promise<unknown>, ok: string) => { setErr(''); setMsg(''); try { await fn(); setMsg(ok); onChange(); } catch (e: any) { setErr(e.message); } };
  const preview = (n: Theme) => { setTheme(n); applyTheme(n); };           // live preview, saved only on "Save theme"
  const saveTheme = () => run(() => api('/api/branding/theme', 'PUT', theme), 'Theme saved.');
  const revert = () => preview(saved);
  const dirty = !same(theme, saved);

  async function upload(f?: File) {
    if (!f) return;
    if (f.size > 256 * 1024) { setErr('Logo is too large (max 256 KB).'); return; }
    run(async () => api('/api/branding/logo', 'PUT', { dataUrl: await fileToDataUrl(f) }), 'Logo updated.');
  }
  const col = (k: keyof Theme, label: string, fallback: string) => (
    <label>{label}
      <input type="color" value={theme[k] || fallback} onChange={(e) => preview({ ...theme, [k]: e.target.value })} />
      {theme[k] && <button type="button" className="link" onClick={() => preview({ ...theme, [k]: null })}>default</button>}
    </label>
  );

  return (
    <section>
      <h2>Settings</h2>
      <p className="muted">Make the dashboard yours. By default you get ConnectFlow green; change the colours, logo and website any time.</p>
      {err && <div className="err">{err}</div>}{msg && <div className="okbox">{msg}</div>}

      <div className="card stack">
        <b>Workspace</b>
        <div className="row"><input style={{ flex: 1, minWidth: 200 }} value={name} onChange={(e) => setName(e.target.value)} maxLength={120} aria-label="Workspace name" />
          <button onClick={() => run(() => api('/api/workspace', 'PATCH', { name }), 'Name saved.')} disabled={name.trim().length < 2 || name === t.name}>Save name</button></div>
        <div className="row"><input style={{ flex: 1, minWidth: 200 }} value={site} onChange={(e) => setSite(e.target.value)} placeholder="Your website, e.g. yourcompany.com" aria-label="Website" />
          <button onClick={() => run(async () => { const r = await api<{ website: string }>('/api/workspace/website', 'PUT', { website: site }); setSite(r.website); }, 'Website saved. It now shows in your sidebar.')} disabled={site === (t.website || '')}>Save website</button></div>
        <small className="muted">The website appears as “Visit website” in your dashboard sidebar.</small>
      </div>

      <div className="card stack" style={{ marginTop: 14 }}>
        <b>Theme</b>
        <div className="themes">
          {THEMES.map((th) => (
            <button key={th.name} type="button" className={`theme${same(theme, th.t) ? ' on' : ''}`} onClick={() => preview(th.t)} style={{ ['--a' as any]: th.t.accent || DEFAULT_BRAND }}>
              <span className="mini"><i style={{ background: th.t.sidebar || '#14221a' }} /><b style={{ background: th.t.page || '#eef2ef' }} /></span>
              <span>{th.name}</span>
            </button>
          ))}
        </div>
        <div className="pickrow">
          {col('accent', 'Buttons & highlights', DEFAULT_BRAND)}
          {col('sidebar', 'Sidebar', '#14221a')}
          {col('page', 'Page background', '#f4f6fb')}
        </div>
        <div className="swatches">{PRESETS.map((p) => <button key={p.name} className={`swatch${theme.accent === p.color ? ' on' : ''}`} style={{ background: p.color || DEFAULT_BRAND }} title={p.name} aria-label={p.name} onClick={() => preview({ ...theme, accent: p.color })} />)}</div>
        <div className="preview"><span className="primary pv">Primary button</span><span className="pv-link">Link</span><span className="badge ok">Badge</span><div className="meterbar" style={{ flex: 1 }}><i style={{ width: '62%' }} /></div></div>
        <div className="row"><button className="primary" onClick={saveTheme} disabled={!dirty}>Save theme</button><button onClick={revert} disabled={!dirty}>Undo changes</button>
          <button onClick={() => { preview({ accent: null, sidebar: null, page: null }); run(() => api('/api/branding/theme', 'PUT', { accent: null, sidebar: null, page: null }), 'Back to the default look.'); }}>Reset to default</button></div>
        <small className="muted">Text colours adjust automatically so everything stays readable. Pick any light or dark page colour.</small>
      </div>

      <div className="card stack" style={{ marginTop: 14 }}>
        <b>Logo</b>
        <p className="muted" style={{ margin: 0 }}>Shown in the sidebar instead of the ConnectFlow logo. PNG, JPEG, WebP or SVG, up to 256 KB. A square image works best.</p>
        <div className="row">
          <div className="logoprev">{t.brand.logo ? <img src={t.brand.logo} alt="Your logo" /> : <span className="muted">No logo</span>}</div>
          <input ref={file} type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" hidden onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ''; }} />
          <button onClick={() => file.current?.click()}>{t.brand.logo ? 'Replace logo' : 'Upload logo'}</button>
          {t.brand.logo && <button className="danger" onClick={() => run(() => api('/api/branding/logo', 'DELETE'), 'Logo removed.')}>Remove</button>}
        </div>
      </div>

      <SecurityCard onChange={onChange} />
      {me.role === 'owner' && <DataCard />}
    </section>
  );
}
