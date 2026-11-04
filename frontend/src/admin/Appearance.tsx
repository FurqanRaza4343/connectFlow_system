import { useState } from 'react';
import { ADMIN_PRESETS, AdminTheme, adminSwatch, applyAdminTheme, loadTheme, saveTheme } from './adminTheme';

const same = (a: AdminTheme, b: AdminTheme) => a.accent === b.accent && a.sidebar === b.sidebar && a.page === b.page;

export function Appearance() {
  const [saved, setSaved] = useState<AdminTheme>(loadTheme());
  const [t, setT] = useState<AdminTheme>(saved);
  const preview = (n: AdminTheme) => { setT(n); applyAdminTheme(n); };
  const save = () => { saveTheme(t); setSaved(t); };
  const col = (k: keyof AdminTheme, label: string, fallback: string) => (
    <label>{label}<input type="color" value={t[k] || fallback} onChange={(e) => preview({ ...t, [k]: e.target.value })} />
      {t[k] && <button className="ad-btn sm" onClick={() => preview({ ...t, [k]: null })}>default</button>}</label>
  );
  return (
    <div>
      <div className="ad-head"><div><h2>Appearance</h2><p>Make the admin dashboard look the way you like. Saved in this browser.</p></div></div>
      <div className="ad-card">
        <h3>Themes</h3>
        <div className="ad-themes">
          {ADMIN_PRESETS.map((p) => { const sw = adminSwatch(p.t); return (
            <button key={p.name} className={`ad-theme${same(t, p.t) ? ' on' : ''}`} onClick={() => preview(p.t)} style={{ ['--a' as any]: sw.accent }}>
              <span className="mini"><i style={{ background: sw.side }} /><b style={{ background: sw.page }} /></span><span>{p.name}</span>
            </button>); })}
        </div>
        <h3 style={{ marginTop: 18 }}>Custom colours</h3>
        <div className="ad-pick">{col('accent', 'Accent', '#2ed47a')}{col('sidebar', 'Sidebar', '#111815')}{col('page', 'Page background', '#0a0e0c')}</div>
        <div className="ad-row" style={{ marginTop: 16 }}>
          <button className="ad-btn pri" onClick={save} disabled={same(t, saved)}>Save theme</button>
          <button className="ad-btn" onClick={() => preview(saved)} disabled={same(t, saved)}>Undo</button>
          <button className="ad-btn" onClick={() => { const d = { accent: null, sidebar: null, page: null }; preview(d); saveTheme(d); setSaved(d); }}>Reset to default</button>
        </div>
        <p className="ad-muted" style={{ marginBottom: 0 }}>Text colours adjust automatically so every page stays readable, light or dark.</p>
      </div>
    </div>
  );
}
