// Workspace theme: accent, sidebar and page colours are CSS variables, so the whole app re-themes instantly.
// Default (nothing saved) = ConnectFlow green and the browser's light/dark preference.
export const DEFAULT_BRAND = '#16a34a';
export type Theme = { accent: string | null; sidebar: string | null; page: string | null };

export const PRESETS: Array<{ name: string; color: string | null }> = [
  { name: 'ConnectFlow green', color: null },
  { name: 'Blue', color: '#2563eb' },
  { name: 'Indigo', color: '#4f46e5' },
  { name: 'Violet', color: '#7c3aed' },
  { name: 'Rose', color: '#e11d48' },
  { name: 'Orange', color: '#ea580c' },
  { name: 'Teal', color: '#0d9488' },
  { name: 'Slate', color: '#475569' },
];

// One-click looks: accent + sidebar + page
export const THEMES: Array<{ name: string; t: Theme }> = [
  { name: 'Default', t: { accent: null, sidebar: null, page: null } },
  { name: 'Clean light', t: { accent: '#2563eb', sidebar: '#ffffff', page: '#f4f6fb' } },
  { name: 'Midnight', t: { accent: '#818cf8', sidebar: '#0b1020', page: '#111729' } },
  { name: 'Ocean', t: { accent: '#0ea5e9', sidebar: '#0c2a43', page: '#eef5fb' } },
  { name: 'Forest', t: { accent: '#16a34a', sidebar: '#0f2a1d', page: '#f3f8f4' } },
  { name: 'Sand', t: { accent: '#c2410c', sidebar: '#3b2f25', page: '#faf6ef' } },
  { name: 'Graphite', t: { accent: '#f59e0b', sidebar: '#18181b', page: '#27272a' } },
];

const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const lum = (hex: string) => {
  const [r, g, b] = rgb(hex).map((v) => v / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const mixHex = (a: string, b: string, amt: number) => '#' + rgb(a).map((v, i) => Math.round(v * (1 - amt) + rgb(b)[i] * amt).toString(16).padStart(2, '0')).join('');
const valid = (c?: string | null): c is string => !!c && /^#[0-9a-f]{6}$/i.test(c);

const VARS = ['--brand', '--brand-hover', '--on-brand', '--brand-soft', '--side-bg', '--side-text', '--side-muted', '--side-hover', '--side-line', '--bg', '--card', '--text', '--muted', '--line'];

export function applyTheme(t: Theme) {
  const root = document.documentElement.style;
  VARS.forEach((k) => root.removeProperty(k));
  root.removeProperty('color-scheme');
  if (valid(t.accent)) {
    root.setProperty('--brand', t.accent);
    root.setProperty('--brand-hover', mixHex(t.accent, lum(t.accent) > 0.4 ? '#000000' : '#ffffff', 0.12));
    root.setProperty('--on-brand', lum(t.accent) > 0.45 ? '#0b1410' : '#ffffff');
    root.setProperty('--brand-soft', t.accent + '26');
  }
  if (valid(t.sidebar)) {
    const light = lum(t.sidebar) > 0.45;
    const text = light ? '#0f172a' : '#e8efeb';
    root.setProperty('--side-bg', t.sidebar);
    root.setProperty('--side-text', text);
    root.setProperty('--side-muted', mixHex(text, t.sidebar, 0.38));
    root.setProperty('--side-hover', mixHex(t.sidebar, text, 0.09));
    root.setProperty('--side-line', mixHex(t.sidebar, text, 0.14));
  }
  if (valid(t.page)) {
    const light = lum(t.page) > 0.45;
    const text = light ? '#0f172a' : '#eef2ef';
    root.setProperty('--bg', t.page);
    root.setProperty('--text', text);
    root.setProperty('--muted', mixHex(text, t.page, 0.42));
    root.setProperty('--card', light ? mixHex(t.page, '#ffffff', 0.7) : mixHex(t.page, '#ffffff', 0.06));
    root.setProperty('--line', mixHex(t.page, text, light ? 0.13 : 0.14));
    root.setProperty('color-scheme', light ? 'light' : 'dark');
  }
}

export const applyBrand = (color: string | null | undefined) => applyTheme({ accent: color ?? null, sidebar: null, page: null });

export function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(new Error('Could not read the file'));
    r.readAsDataURL(file);
  });
}
