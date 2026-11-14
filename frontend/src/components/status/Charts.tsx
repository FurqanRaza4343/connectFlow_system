import { Stats, THEME_COLOR, THEME_NAME, themeKey } from './shared';

type Series = Stats['series'];

const W = 640, H = 220, PAD = { l: 30, r: 8, t: 16, b: 34 };

/** Rectangle with only the top corners rounded (the topmost segment of a stacked bar). */
const topRound = (x: number, y: number, w: number, h: number, r: number) => {
  const rr = Math.min(r, h, w / 2);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
};

/** Stacked bars: posted / planned / failed per day. Text is always in the ink tokens, never in a series colour. */
export function BarChart({ series, today }: { series: Series; today: string }) {
  const max = Math.max(4, ...series.map((s) => s.posted + s.planned + s.failed));
  const bw = (W - PAD.l - PAD.r) / series.length;
  const inner = H - PAD.t - PAD.b;
  const y = (v: number) => PAD.t + inner * (1 - v / max);
  const ti = series.findIndex((s) => s.day === today);
  const segs: Array<['posted' | 'planned' | 'failed', string, string]> = [['posted', 'var(--ok)', 'posted'], ['planned', 'var(--blue)', 'planned'], ['failed', 'var(--red)', 'failed']];
  return (
    <>
      <div className="st-chartwrap">
        <svg className="st-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Posts per day: posted, planned and failed">
          {[0, 0.5, 1].map((f) => { const v = Math.round(max * f); return (
            <g key={f}><line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} stroke="var(--line)" strokeWidth={1} /><text x={PAD.l - 5} y={y(v) + 3} textAnchor="end" fontSize={10} fill="var(--muted)">{v}</text></g>
          ); })}
          {series.map((s, i) => {
            const x = PAD.l + i * bw + bw * 0.18, w = bw * 0.64;
            const parts = segs.filter(([k]) => s[k] > 0);
            let top = H - PAD.b;
            return (
              <g key={s.day}>
                {parts.map(([k, color, label], pi) => {
                  const h = Math.max(3, inner * (s[k] / max) - (pi < parts.length - 1 ? 2 : 0)); // 2 px surface gap between stacked segments
                  top -= h + (pi < parts.length - 1 ? 2 : 0);
                  const last = pi === parts.length - 1;
                  return <path key={k} d={topRound(x, top, w, h, last ? 4 : 0)} style={{ fill: color }}><title>{`${s.day}: ${s[k]} ${label}`}</title></path>;
                })}
                {(i % 3 === 0 || i === series.length - 1) && <text x={x + w / 2} y={H - 14} textAnchor="middle" fontSize={10} fill="var(--muted)">{s.day.slice(5)}</text>}
              </g>
            );
          })}
          {ti >= 0 && (
            <g><line x1={PAD.l + ti * bw + bw / 2} x2={PAD.l + ti * bw + bw / 2} y1={PAD.t} y2={H - PAD.b} stroke="var(--muted)" strokeWidth={1} strokeDasharray="3 3" />
              <text x={PAD.l + ti * bw + bw / 2} y={PAD.t - 4} textAnchor="middle" fontSize={9} fill="var(--muted)">today</text></g>
          )}
        </svg>
      </div>
      <div className="st-legend"><span><i style={{ background: 'var(--ok)' }} />Posted</span><span><i style={{ background: 'var(--blue)' }} />Planned</span><span><i style={{ background: 'var(--red)' }} />Failed</span></div>
    </>
  );
}

export function BarTable({ series }: { series: Series }) {
  const rows = series.filter((s) => s.posted + s.planned + s.failed);
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Day</th><th>Posted</th><th>Planned</th><th>Failed</th></tr></thead>
        <tbody>
          {rows.map((s) => <tr key={s.day}><td>{s.day}</td><td>{s.posted}</td><td>{s.planned}</td><td>{s.failed}</td></tr>)}
          {!rows.length && <tr><td colSpan={4} className="muted">No posts in this period.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

/** Content mix by theme, with a FIXED colour per theme. */
export function Donut({ themes }: { themes: Record<string, number> }) {
  const agg: Record<string, number> = {};
  for (const [k, v] of Object.entries(themes)) agg[themeKey(k)] = (agg[themeKey(k)] || 0) + v;
  const order = [...Object.keys(THEME_COLOR), 'other'].filter((k) => agg[k]);
  const total = order.reduce((a, k) => a + agg[k], 0);
  if (!total) return <p className="muted">No posts yet.</p>;
  const R = 54, C = 2 * Math.PI * R;
  let off = 0;
  return (
    <div className="st-donut-wrap">
      <svg className="st-donut" viewBox="0 0 150 150" role="img" aria-label="Posts by theme">
        {order.map((k) => {
          const len = (agg[k] / total) * C, draw = Math.max(0, len - 2), o = off;
          off += len;
          return <circle key={k} cx={75} cy={75} r={R} fill="none" style={{ stroke: THEME_COLOR[k] ?? 'var(--muted)' }} strokeWidth={18} strokeDasharray={`${draw.toFixed(2)} ${(C - draw).toFixed(2)}`} strokeDashoffset={(-o).toFixed(2)} transform="rotate(-90 75 75)"><title>{`${THEME_NAME[k]}: ${agg[k]}`}</title></circle>;
        })}
        <text x={75} y={72} textAnchor="middle" fontSize={22} fontWeight={700} fill="var(--text)">{total}</text>
        <text x={75} y={90} textAnchor="middle" fontSize={10} fill="var(--muted)">posts</text>
      </svg>
      <div className="st-legend col">
        {order.map((k) => <span key={k}><i style={{ background: THEME_COLOR[k] ?? 'var(--muted)' }} />{THEME_NAME[k]} · {agg[k]} ({Math.round((agg[k] / total) * 100)}%)</span>)}
      </div>
    </div>
  );
}
