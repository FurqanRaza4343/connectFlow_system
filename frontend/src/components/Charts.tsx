// Plain-SVG charts (no libraries). Colours come from CSS variables so they follow the workspace theme.
import { useState } from 'react';

export type Slice = { key: string; label: string; value: number; color: string };
export type Pt = { day: string; sent: number; replies: number };

/** Ring chart with rounded gaps and a centre label. */
export function Donut({ slices, size = 164, center }: { slices: Slice[]; size?: number; center?: { big: string; small: string } }) {
  const total = slices.reduce((a, s) => a + s.value, 0);
  const r = 54, c = 2 * Math.PI * r;
  let off = 0;
  return (
    <svg viewBox="0 0 140 140" width={size} height={size} role="img" aria-label={`Lead breakdown: ${slices.filter((s) => s.value).map((s) => `${s.label} ${s.value}`).join(', ') || 'no leads'}`}>
      <circle cx="70" cy="70" r={r} fill="none" stroke="var(--line)" strokeWidth="13" />
      {total > 0 && slices.filter((s) => s.value > 0).map((s) => {
        const len = (s.value / total) * c, gap = slices.filter((x) => x.value > 0).length > 1 ? 3 : 0;
        const el = <circle key={s.key} cx="70" cy="70" r={r} fill="none" stroke={s.color} strokeWidth="13" strokeLinecap="round" strokeDasharray={`${Math.max(0.5, len - gap)} ${c}`} strokeDashoffset={-off} transform="rotate(-90 70 70)" />;
        off += len;
        return el;
      })}
      {center && <><text x="70" y="69" textAnchor="middle" fontSize="26" fontWeight="700" fill="var(--text)">{center.big}</text><text x="70" y="86" textAnchor="middle" fontSize="10.5" fill="var(--muted)">{center.small}</text></>}
    </svg>
  );
}

// ---- smooth curve through points (Catmull-Rom -> cubic Bezier)
function smooth(pts: Array<[number, number]>): string {
  if (pts.length < 2) return '';
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2, t = 0.18;
    d += ` C${p1[0] + (p2[0] - p0[0]) * t},${p1[1] + (p2[1] - p0[1]) * t} ${p2[0] - (p3[0] - p1[0]) * t},${p2[1] - (p3[1] - p1[1]) * t} ${p2[0]},${p2[1]}`;
  }
  return d;
}
const niceMax = (v: number) => { const m = Math.max(4, v); const p = 10 ** Math.floor(Math.log10(m)); return Math.ceil(m / (p / 2)) * (p / 2); };

/** Two smooth lines with gradient fills, dashed grid and a hover read-out. */
export function ActivityChart({ data, height = 262 }: { data: Pt[]; height?: number }) {
  const [hi, setHi] = useState<number | null>(null);
  const W = 640, H = height, L = 34, R = 12, T = 14, B = 26;
  const max = niceMax(Math.max(...data.map((d) => Math.max(d.sent, d.replies))));
  const x = (i: number) => L + (i * (W - L - R)) / Math.max(1, data.length - 1);
  const y = (v: number) => T + (H - T - B) * (1 - v / max);
  const sent = data.map((d, i): [number, number] => [x(i), y(d.sent)]);
  const rep = data.map((d, i): [number, number] => [x(i), y(d.replies)]);
  const area = (p: Array<[number, number]>) => `${smooth(p)} L${p[p.length - 1][0]},${H - B} L${p[0][0]},${H - B} Z`;
  const ticks = [0, 1, 2, 3, 4].map((k) => Math.round((max * k) / 4));
  const label = (iso: string) => { const [, m, d] = iso.split('-'); return `${d}/${m}`; };
  const pick = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    setHi(Math.max(0, Math.min(data.length - 1, Math.round(((px - L) / (W - L - R)) * (data.length - 1)))));
  };
  const empty = data.every((d) => !d.sent && !d.replies);
  return (
    <div className="chart-box">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Messages sent and replies over the last 14 days" onMouseMove={pick} onMouseLeave={() => setHi(null)} style={{ display: 'block' }}>
        <defs>
          <linearGradient id="gSent" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--brand)" stopOpacity=".32" /><stop offset="100%" stopColor="var(--brand)" stopOpacity="0" /></linearGradient>
          <linearGradient id="gRep" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#f59e0b" stopOpacity=".25" /><stop offset="100%" stopColor="#f59e0b" stopOpacity="0" /></linearGradient>
        </defs>
        {ticks.map((t) => <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeDasharray={t ? '3 4' : undefined} /><text x={L - 8} y={y(t) + 3.5} textAnchor="end" fontSize="10" fill="var(--muted)">{t}</text></g>)}
        {data.map((d, i) => (i % 2 === 0 || i === data.length - 1) && <text key={d.day} x={x(i)} y={H - 7} textAnchor="middle" fontSize="10" fill="var(--muted)">{label(d.day)}</text>)}
        {!empty && <>
          <path d={area(rep)} fill="url(#gRep)" /><path d={smooth(rep)} fill="none" stroke="#f59e0b" strokeWidth="2.2" strokeLinecap="round" />
          <path d={area(sent)} fill="url(#gSent)" /><path d={smooth(sent)} fill="none" stroke="var(--brand)" strokeWidth="2.6" strokeLinecap="round" />
        </>}
        {hi !== null && <g>
          <line x1={x(hi)} x2={x(hi)} y1={T} y2={H - B} stroke="var(--muted)" strokeOpacity=".5" strokeDasharray="3 3" />
          <circle cx={x(hi)} cy={y(data[hi].sent)} r="4.5" fill="var(--card)" stroke="var(--brand)" strokeWidth="2.4" />
          <circle cx={x(hi)} cy={y(data[hi].replies)} r="4.5" fill="var(--card)" stroke="#f59e0b" strokeWidth="2.4" />
        </g>}
      </svg>
      {empty && <div className="chart-empty">No messages yet. Your first batch will appear here.</div>}
      {hi !== null && <div className="chart-tip" style={{ left: `${(x(hi) / W) * 100}%`, transform: `translateX(${hi > data.length / 2 ? '-105%' : '5%'})` }}>
        <b>{label(data[hi].day)}</b><span><i style={{ background: 'var(--brand)' }} />{data[hi].sent} sent</span><span><i style={{ background: '#f59e0b' }} />{data[hi].replies} replies</span>
      </div>}
    </div>
  );
}

/** Tiny trend line for KPI cards. */
export function Sparkline({ values, color = 'var(--brand)', w = 70, h = 28 }: { values: number[]; color?: string; w?: number; h?: number }) {
  const max = Math.max(1, ...values);
  const pts = values.map((v, i): [number, number] => [(i * (w - 4)) / Math.max(1, values.length - 1) + 2, h - 3 - (v / max) * (h - 8)]);
  if (values.every((v) => !v)) return <svg width={w} height={h} aria-hidden="true"><line x1="2" x2={w - 2} y1={h - 3} y2={h - 3} stroke="var(--line)" strokeWidth="2" strokeLinecap="round" /></svg>;
  return (
    <svg width={w} height={h} aria-hidden="true">
      <defs><linearGradient id="sp" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity=".28" /><stop offset="100%" stopColor={color} stopOpacity="0" /></linearGradient></defs>
      <path d={`${smooth(pts)} L${pts[pts.length - 1][0]},${h} L${pts[0][0]},${h} Z`} fill="url(#sp)" /><path d={smooth(pts)} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

/** Horizontal funnel: each step as a bar relative to the first, with conversion from the previous step. */
export function Funnel({ steps }: { steps: Array<{ label: string; value: number; color: string; hint?: string }> }) {
  const top = Math.max(1, steps[0]?.value || 1);
  return (
    <div className="funnel">
      {steps.map((s, i) => {
        const prev = i ? steps[i - 1].value : 0;
        const conv = i && prev ? Math.round((s.value / prev) * 100) : null;
        return (
          <div className="fn-row" key={s.label}>
            <div className="fn-head"><span>{s.label}{s.hint && <small className="muted"> · {s.hint}</small>}</span><span><b>{s.value.toLocaleString()}</b>{conv !== null && <small className="muted"> {conv}%</small>}</span></div>
            <div className="fn-track"><div className="fn-bar" style={{ width: `${Math.max(s.value ? 3 : 0, (s.value / top) * 100)}%`, background: s.color }} /></div>
          </div>
        );
      })}
    </div>
  );
}

// kept for any other page that still imports it
export function SentReplies({ data }: { data: Pt[]; height?: number }) { return <ActivityChart data={data} />; }
