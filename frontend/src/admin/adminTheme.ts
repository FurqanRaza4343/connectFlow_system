// Platform-admin theme: default ConnectFlow green. Customisable (accent, sidebar, page) and remembered in this browser.
export type AdminTheme = { accent: string | null; sidebar: string | null; page: string | null };
export const ADMIN_PRESETS: Array<{ name: string; t: AdminTheme }> = [
  { name: 'ConnectFlow green', t: { accent: null, sidebar: null, page: null } },
  { name: 'Indigo (classic admin)', t: { accent: '#6366f1', sidebar: '#111830', page: '#0b1020' } },
  { name: 'Ocean', t: { accent: '#0ea5e9', sidebar: '#0c2a43', page: '#08141f' } },
  { name: 'Graphite', t: { accent: '#f59e0b', sidebar: '#18181b', page: '#101012' } },
  { name: 'Clean light', t: { accent: '#2563eb', sidebar: '#ffffff', page: '#f4f6fb' } },
  { name: 'Sand', t: { accent: '#c2410c', sidebar: '#3b2f25', page: '#faf6ef' } },
];
const KEY = 'cf_admin_theme';
const rgb = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const lum = (h: string) => { const [r, g, b] = rgb(h).map((v) => v / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const mix = (a: string, b: string, t: number) => '#' + rgb(a).map((v, i) => Math.round(v * (1 - t) + rgb(b)[i] * t).toString(16).padStart(2, '0')).join('');
const ok = (c?: string | null): c is string => !!c && /^#[0-9a-f]{6}$/i.test(c);
const VARS = ['--t-bg', '--t-panel', '--t-panel2', '--t-line', '--t-text', '--t-muted', '--t-faint', '--t-ac', '--t-ac2', '--t-on-ac', '--t-input', '--t-navon', '--t-glow', '--t-side'];

export function loadTheme(): AdminTheme {
  try { const t = JSON.parse(localStorage.getItem(KEY) || 'null'); if (t) return { accent: ok(t.accent) ? t.accent : null, sidebar: ok(t.sidebar) ? t.sidebar : null, page: ok(t.page) ? t.page : null }; } catch { /* ignore */ }
  return { accent: null, sidebar: null, page: null };
}
export function saveTheme(t: AdminTheme) { try { localStorage.setItem(KEY, JSON.stringify(t)); } catch { /* ignore */ } }

export function applyAdminTheme(t: AdminTheme) {
  const r = document.documentElement.style;
  VARS.forEach((k) => r.removeProperty(k));
  if (ok(t.accent)) { r.setProperty('--t-ac', t.accent); r.setProperty('--t-ac2', mix(t.accent, '#ffffff', 0.18)); r.setProperty('--t-on-ac', lum(t.accent) > 0.45 ? '#0b1410' : '#ffffff'); }
  if (ok(t.page)) {
    const light = lum(t.page) > 0.45, text = light ? '#0f172a' : '#eef2ef';
    r.setProperty('--t-bg', t.page); r.setProperty('--t-text', text); r.setProperty('--t-muted', mix(text, t.page, 0.42)); r.setProperty('--t-faint', mix(text, t.page, 0.62));
    r.setProperty('--t-panel', light ? mix(t.page, '#ffffff', 0.7) : mix(t.page, '#ffffff', 0.06)); r.setProperty('--t-panel2', light ? mix(t.page, '#ffffff', 0.4) : mix(t.page, '#ffffff', 0.1));
    r.setProperty('--t-line', mix(t.page, text, light ? 0.13 : 0.14)); r.setProperty('--t-input', light ? '#ffffff' : mix(t.page, '#000000', 0.25));
    r.setProperty('--t-navon', mix(t.page, ok(t.accent) ? t.accent : '#2ed47a', 0.18)); r.setProperty('--t-glow', mix(t.page, ok(t.accent) ? t.accent : '#2ed47a', 0.2));
  }
  if (ok(t.sidebar)) r.setProperty('--t-side', t.sidebar);
}
export const adminSwatch = (t: AdminTheme) => ({ side: t.sidebar || '#111815', page: t.page || '#0a0e0c', accent: t.accent || '#2ed47a' });
