import { useState } from 'react';
import { api } from '../../api';
import { Capabilities, errText, fmtStamp, imageUrl, Notify, Post, STATE_LABEL, THEME_NAME } from './shared';

export const canAct = (p: Post) => ['draft', 'scheduled', 'failed'].includes(p.state);

/** The row/modal actions shared by the Posts tab and the planner's post dialog. Every call asks first when it is destructive, then reloads. */
export function usePostOps(cap: Capabilities, notify: Notify, reload: () => void) {
  const [busy, setBusy] = useState('');
  async function run(id: string, fn: () => Promise<string | void>): Promise<boolean> {
    setBusy(id);
    try { const msg = await fn(); if (msg) notify(msg, 'ok'); reload(); return true; } catch (e) { notify(errText(e), 'bad'); return false; } finally { setBusy(''); }
  }
  return {
    busy,
    postNow: (p: Post) => {
      if (!confirm(`Post this Status now?\n\nIt becomes visible to your WhatsApp contacts for 24 hours.${cap.dryRun ? '\n\nDry run is ON: nothing will actually be sent.' : ''}`)) return Promise.resolve(false);
      return run(p.id, async () => { const r = await api<{ dryRun?: boolean }>(`/api/status/posts/${p.id}/post`, 'POST'); return r.dryRun ? 'Dry run: nothing was sent to WhatsApp' : 'Posted'; });
    },
    cancel: (p: Post) => run(p.id, async () => { await api(`/api/status/posts/${p.id}/cancel`, 'POST'); return 'Cancelled'; }),
    delWa: (p: Post) => (confirm('Delete this Status from WhatsApp?\n\nIt disappears for everyone who could see it.') ? run(p.id, async () => { await api(`/api/status/posts/${p.id}/delete-wa`, 'POST'); return 'Deleted from WhatsApp'; }) : Promise.resolve(false)),
    remove: (p: Post) => (confirm('Remove this post from the list?\n\nA posted Status stays on WhatsApp until it expires.') ? run(p.id, async () => { await api(`/api/status/posts/${p.id}`, 'DELETE'); return 'Removed from the list'; }) : Promise.resolve(false)),
    rewrite: (p: Post) => run(p.id, async () => { await api(`/api/status/posts/${p.id}/rewrite`, 'POST', { instruction: '' }); return 'Rewritten'; }),
  };
}
export type PostOps = ReturnType<typeof usePostOps>;

export function Thumb({ p, w = 62 }: { p: Post; w?: number }) {
  const style = { width: w, height: Math.round((w * 16) / 9) };
  return p.hasImage
    ? <img className="st-thumb" style={style} src={imageUrl(p.id, p.postedAt || p.scheduledAt || p.createdAt)} alt="" loading="lazy" />
    : <div className="st-thumb st-thumb-empty" style={style}>text</div>;
}

export function Posts({ posts, cap, canEdit, ops, onEdit, onRepost }: { posts: Post[]; cap: Capabilities; canEdit: boolean; ops: PostOps; onEdit: (p: Post) => void; onRepost: (p: Post) => void }) {
  if (!posts.length) return <div className="card"><h2>Posts</h2><p className="muted">Nothing yet. Create your first Status in the Create tab.</p></div>;
  const tz = cap.timezone;
  return (
    <div className="card">
      <h2>Posts</h2>
      <div className="st-list">
        {posts.map((p) => {
          const when = p.state === 'scheduled' && p.scheduledAt ? `for ${fmtStamp(p.scheduledAt, tz)}` : p.state === 'posted' && p.postedAt ? `at ${fmtStamp(p.postedAt, tz)} (stays 24 h)` : '';
          const b = ops.busy === p.id;
          return (
            <div className="st-row" key={p.id}>
              <Thumb p={p} />
              <div className="st-row-main">
                <div className="st-row-title">
                  <b>{p.headline || p.caption.slice(0, 60) || '(no title)'}</b>
                  <span className={`st-badge ${p.state}`}>{STATE_LABEL[p.state]}</span>
                  {p.auto && <span className="st-chip">autopilot</span>}
                  {p.campaignId && <span className="st-chip blue">campaign{p.planDay ? ` · day ${p.planDay}` : ''}</span>}
                  {p.planTheme && <span className="st-chip">{THEME_NAME[p.planTheme] ?? p.planTheme}</span>}
                  {p.btnLink && <span className="st-chip">link</span>}
                </div>
                {when && <small className="muted">{when}</small>}
                {p.error && <small className="st-err">{p.error}</small>}
                {canEdit && (
                  <div className="st-btns">
                    {canAct(p) && <button className="primary" disabled={b} onClick={() => ops.postNow(p)}>Post now</button>}
                    {canAct(p) && <button disabled={b} onClick={() => onEdit(p)}>Edit</button>}
                    {(p.state === 'scheduled' || p.state === 'draft') && <button disabled={b} onClick={() => ops.cancel(p)}>Cancel</button>}
                    {p.state === 'posted' && <button disabled={b} title="WhatsApp cannot edit a posted Status: this opens a copy in the composer to post again." onClick={() => onRepost(p)}>Copy &amp; repost</button>}
                    {p.state === 'posted' && p.waId && <button className="danger" disabled={b} onClick={() => ops.delWa(p)}>Delete from WhatsApp</button>}
                    {p.state !== 'posting' && <button disabled={b} title="Only removes it from this list. It does NOT delete anything on WhatsApp." onClick={() => ops.remove(p)}>Remove from list</button>}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
