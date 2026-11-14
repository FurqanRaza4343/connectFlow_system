import { useEffect, useState } from 'react';

export type PostState = 'draft' | 'scheduled' | 'posting' | 'posted' | 'failed' | 'cancelled';

export type Post = {
  id: string; mode: string; kind: string; state: PostState; template: string; headline: string; sub: string; badge: string; cta: string; caption: string; link: string;
  prompt: string; imagePrompt: string; hasImage: boolean; hasBg: boolean; bgUpload: boolean; accent: string; dark: string; ink: string; btnLink: string; overlay: number;
  pos: 'top' | 'center' | 'bottom'; plain: boolean; campaignId: string | null; planTheme: string; planDay: number; scheduledAt: string | null; postedAt: string | null;
  expiresAt: string | null; error: string; tries: number; auto: boolean; waId: boolean; createdAt: string;
};

export type Profile = {
  instance_id: string | null; auto: boolean; times: string; per_day: number; approval: string; image_style: string; image_daily_cap: number; image_monthly_cap: number;
  language: string; footer: string; accent: string; dark: string; ink: string; button_text: string; button_link: string; wa_number: string; wa_text: string;
  templates: string; offers: string; recipients: string;
};

export type Usage = {
  today: { images: number; copies: number; posted: number };
  month: { images: number; copies: number; posted: number; costUsd: number };
  caps: { imagesPerDay: number; imagesPerMonth: number };
};

export type Capabilities = {
  renderer: boolean; aiImages: boolean; templates: string[]; plan: { postsPerDay: number; autopilot: boolean; campaigns: boolean };
  dryRun: boolean; timezone: string; numberReady: boolean; audience: number; fake: boolean;
};

export type StatusData = { posts: Post[]; usage: Usage; profile: Profile; capabilities: Capabilities };

export type Idea = { headline: string; sub: string; badge: string; cta: string; caption: string; theme: string; imagePrompt: string };

export type CampaignState = 'planning' | 'asking' | 'planned' | 'generating' | 'review' | 'running' | 'paused' | 'done' | 'cancelled' | 'failed';
export type Slot = { n: number; day: number; date: string; time: string; at: string; theme: string; angle: string };
export type Campaign = {
  id: string; name: string; brief: string; goal: string; days: number; intervalHours: number; perDay: number; windowFrom: string; windowTo: string; startDate: string;
  language: string; style: string; state: CampaignState; questions: Array<{ id: string; question: string; options: string[] }>; summary: string; error: string;
  busySince: string | null; total: number; generated: number; counts: Record<string, number>; slots: Slot[]; maxPerDay: number; createdAt: string;
};

export type CalItem = { id: string; state: PostState; at: string; date: string; headline: string; theme: string; campaignId: string | null; hasImage: boolean; error: string };
export type Calendar = { month: string; items: CalItem[] };
export type Stats = {
  today: string; series: Array<{ day: string; posted: number; planned: number; failed: number }>; themes: Record<string, number>; states: Record<string, number>;
  nextAt: string | null; usage: Usage;
};

/** What the composer starts from: a post to edit in place, or a COPY of a posted one. */
export type Seed = { post: Post; repost: boolean } | null;

export type Notify = (text: string, kind?: 'ok' | 'bad') => void;

export const STATE_LABEL: Record<PostState, string> = { draft: 'Draft', scheduled: 'Scheduled', posting: 'Posting…', posted: 'Posted', failed: 'Failed', cancelled: 'Cancelled' };
export const STATE_ICON: Record<string, [string, string, string]> = {
  posted: ['✓', 'Posted', 'var(--ok)'], scheduled: ['●', 'Scheduled', 'var(--blue)'], draft: ['◌', 'Draft', 'var(--muted)'], failed: ['✕', 'Failed', 'var(--red)'], posting: ['…', 'Posting', 'var(--amber)'],
};

export const PRESETS: Array<[string, string]> = [
  ['30% off offer', 'Make a 30% off offer post for our main service, using only my offers list or this request.'],
  ['Our services', 'A clear post about what we do and how a business can start with us.'],
  ['Free consultation', 'Invite people to a free 15-minute consultation call.'],
  ['Useful tip', 'One short, useful tip for business owners about AI and automation.'],
  ['Trust / why us', 'Why businesses can trust us, using only facts from our data.'],
  ['Festival greeting', 'A warm festival greeting from our team (no discount).'],
];
export const PALETTES: Array<[string, string, string]> = [['Ocean', '#004ecb', '#0b1b3a'], ['Emerald', '#0f9d58', '#073b2a'], ['Sunset', '#f97316', '#3b1d0a'], ['Royal', '#7c3aed', '#1e1038'], ['Rose', '#e11d48', '#3a0b17'], ['Gold', '#d4a017', '#16181d']];

export const IDEA_WORDS = ['Reading your company data…', 'Thinking about your audience…', 'Reasoning about the best ideas…', 'Checking your offers…', 'Writing the options…', 'Almost there…'];
export const PLAN_WORDS = ['Reading your company data…', 'Understanding your goal and audience…', 'Thinking about the best content mix…', 'Reasoning about themes and their order…', 'Checking your offers and the rules…', 'Planning the calendar day by day…', 'Writing the strategy summary…', 'Polishing the roadmap…', 'Almost there…'];
export const WRITE_WORDS = ['Writing headlines…', 'Choosing the right words…', 'Drawing the cards…', 'Adding your colours and logo…', 'Checking every post against your offers…', 'Still writing: this takes a minute or two…'];

// Fixed colour per theme (the colour follows the entity, not its rank). Validated for colour-blind separation in light and dark: do not change.
export const THEME_COLOR: Record<string, string> = { tip: '#2563eb', service: '#0d9488', proof: '#7c3aed', offer: '#d97706', greeting: '#db2777', cta: '#65a30d' };
export const THEME_NAME: Record<string, string> = { tip: 'Tip', service: 'Service', proof: 'Trust', offer: 'Offer', greeting: 'Greeting', cta: 'Call to action', other: 'Manual / autopilot' };
export const themeKey = (t: string) => (THEME_COLOR[t] ? t : 'other');
export const themeColor = (t: string) => THEME_COLOR[t] ?? 'var(--muted)';

export const FREQ: Array<[string, string]> = [['i24', 'Every 24 hours (1 a day)'], ['i12', 'Every 12 hours'], ['i8', 'Every 8 hours'], ['i6', 'Every 6 hours'], ['p2', '2 a day'], ['p3', '3 a day']];

export const canEditRole = (role: string) => ['owner', 'admin', 'operator'].includes(role);
export const linkMode = (v: string): 'none' | 'wa' | 'url' => (v === 'wa' ? 'wa' : v ? 'url' : 'none');
export const imageUrl = (id: string, v = '') => `/api/status/posts/${id}/image${v ? `?v=${encodeURIComponent(v)}` : ''}`;
export const clock = (sec: number) => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;

// ---------------------------------------------------------------- time, in the workspace timezone
const safeTz = (tz: string) => { try { new Intl.DateTimeFormat('en', { timeZone: tz }); return tz; } catch { return 'UTC'; } };
export const fmtTime = (iso: string, tz: string) => new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: safeTz(tz) });
export const fmtDay = (iso: string, tz: string) => new Date(iso).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: safeTz(tz) });
export const fmtStamp = (iso: string, tz: string) => `${fmtDay(iso, tz)} ${fmtTime(iso, tz)}`;
export const monthLabel = (m: string) => new Date(`${m}-01T12:00:00Z`).toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' });
export const addMonth = (m: string, n: number) => { const [y, mo] = m.split('-').map(Number); return new Date(Date.UTC(y, mo - 1 + n, 1)).toISOString().slice(0, 7); };
export const tomorrowIn = (tz: string) => new Intl.DateTimeFormat('en-CA', { timeZone: safeTz(tz), year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(Date.now() + 86_400_000));

const toMin = (h: string) => { const [a, b] = String(h).split(':').map(Number); return (a || 0) * 60 + (b || 0); };
/** Posts per day for a frequency + window (same rule as the server). */
export function perDayFor(freq: string, from: string, to: string): number {
  const a = toMin(from), b = toMin(to);
  if (!(b > a)) return 0;
  if (freq.startsWith('i')) { const step = Number(freq.slice(1)) * 60; let n = 0; for (let m = a; m <= b && n < 5; m += step) n++; return n; }
  return Math.min(5, Number(freq.slice(1)));
}

/** Seconds since `since` (epoch ms; null = idle). Ticks once a second and clears its timer on unmount or when `since` becomes null. */
export function useElapsed(since: number | null): number {
  const [sec, setSec] = useState(0);
  useEffect(() => {
    if (since === null) { setSec(0); return; }
    const tick = () => setSec(Math.max(0, Math.floor((Date.now() - since) / 1000)));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [since]);
  return sec;
}

export const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
