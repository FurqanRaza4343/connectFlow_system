import { useCallback, useEffect, useRef, useState } from 'react';
import { api, Instance, Me, Via, viaText } from '../api';
import { ago, usePoll } from '../components/util';
import { Draft, DraftCard } from './Approvals';
import { Attachment, AttachmentStrip, KindIcon, MEDIA_WORD, MediaChip } from '../components/media';

type Conv = { leadId: string; company: string; whatsapp: string; status: string; lastMessage: string; lastDirection: 'in' | 'out'; lastAt: string; needsHuman: boolean; instanceId: string | null; via: Via; attachments?: Attachment[]; mediaKind?: string | null };
type Msg = { id: string; direction: 'in' | 'out'; body: string; kind: string; at: string; attachments?: Attachment[]; linkUrl?: string | null; mediaKind?: string | null };
type Thread = { messages: Msg[]; drafts: Draft[]; via: Via };

export function Inbox({ me, go, initialLead }: { me: Me; go: (hash: string) => void; initialLead?: string }) {
  const [filter, setFilter] = useState<'all' | 'needs_human' | 'replied'>('all');
  const [list, setList] = useState<Conv[] | null>(null);
  const [inst, setInst] = useState('');
  const [nums, setNums] = useState<Instance[]>([]);
  const [lead, setLead] = useState<string | null>(initialLead || null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [text, setText] = useState('');
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [dry, setDry] = useState(false);
  const [sending, setSending] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const canEdit = ['owner', 'admin', 'operator'].includes(me.role);

  const loadList = useCallback(() => { api<Conv[]>(`/api/inbox?filter=${filter}&instance_id=${inst}`).then((r) => { setList(r); setErr(''); }).catch((e) => setErr(e.message)); }, [filter, inst]);
  usePoll(loadList, 15000, [filter, inst]);
  usePoll(() => { api<Instance[]>('/api/instances').then(setNums).catch(() => { /* filter is optional */ }); }, 60000);
  usePoll(() => { api<{ dryRun: boolean }>('/api/outreach/status').then((s) => setDry(s.dryRun)).catch(() => { /* ignore */ }); }, 60000);

  const loadThread = useCallback(() => { if (lead) api<Thread>(`/api/leads/${lead}/thread`).then(setThread).catch((e) => setErr(e.message)); }, [lead]);
  useEffect(() => { setThread(null); setText(''); }, [lead]);
  usePoll(loadThread, 10000, [lead]);
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }); }, [thread?.messages.length, thread?.drafts.length]);

  const conv = list?.find((c) => c.leadId === lead);
  const act = async (fn: () => Promise<unknown>, ok = '') => { setErr(''); setMsg(''); try { await fn(); setMsg(ok); loadThread(); loadList(); } catch (e: any) { setErr(e.message); } };
  const approve = (ids: string[]) => act(async () => { await api('/api/drafts/approve', 'POST', { ids }); }, 'Approved. It will be sent shortly.');

  async function send() {
    if (!lead || !text.trim()) return;
    setSending(true);
    await act(async () => { await api(`/api/leads/${lead}/reply`, 'POST', { text: text.trim() }); setText(''); }, 'Reply queued.');
    setSending(false);
  }

  return (
    <div>
      {err && <div className="err">{err}</div>}{msg && <div className="okbox">{msg}</div>}
      <div className={`inbox${lead ? ' has-thread' : ''}`}>
        <aside className="inbox-list card">
          <div className="chips" style={{ margin: '0 0 10px' }}>
            {([['all', 'All'], ['needs_human', 'Needs you'], ['replied', 'Replied']] as const).map(([k, l]) => <button key={k} className={`chipb${filter === k ? ' on' : ''}`} onClick={() => setFilter(k)}>{l}</button>)}
          </div>
          {nums.length > 1 && <select className="num-filter" value={inst} onChange={(e) => setInst(e.target.value)} aria-label="Filter by number"><option value="">All numbers</option>{nums.map((n) => <option key={n.id} value={n.id}>{n.label}{n.phone ? ` · +${n.phone}` : ''}</option>)}</select>}
          {!list && !err && [0, 1, 2].map((i) => <div key={i} className="skeleton" style={{ height: 58, marginBottom: 8 }} />)}
          {list?.map((c) => (
            <button key={c.leadId} className={`conv${lead === c.leadId ? ' on' : ''}`} onClick={() => setLead(c.leadId)}>
              <div className="row between"><b>{c.company}</b><small className="muted">{ago(c.lastAt)}</small></div>
              <div className="conv-last muted">{c.lastDirection === 'out' ? 'You: ' : ''}{c.lastMessage.slice(0, 70)}</div>
              <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>{c.via && <span className="tag via" title={c.via.label}>via {viaText(c.via)}</span>}<MediaChip n={c.attachments?.length} kind={c.mediaKind} />{c.needsHuman && <span className="badge bad">needs you</span>}<span className={`badge ${c.status === 'stop' ? 'bad' : c.status === 'replied' ? 'ok' : 'warn'}`}>{c.status.replace('_', ' ')}</span></div>
            </button>
          ))}
          {list && !list.length && <p className="muted" style={{ textAlign: 'center', padding: '24px 8px' }}>{filter === 'all' ? 'No conversations yet. Replies from your leads appear here.' : 'Nothing in this view.'}</p>}
        </aside>

        <section className="inbox-thread card">
          {!lead && <div className="muted thread-empty">Choose a conversation to read and reply.</div>}
          {lead && (
            <>
              <div className="row between thread-head">
                <div className="row" style={{ gap: 10 }}><button className="ghost back-btn" onClick={() => setLead(null)} aria-label="Back to conversations">←</button><div><b>{conv?.company || 'Conversation'}</b><div className="muted" style={{ fontSize: 12.5 }}>{conv?.whatsapp ? `+${conv.whatsapp}` : ''}{thread?.via ? ` · via ${viaText(thread.via)} (${thread.via.label})` : ''}{thread?.via && thread.via.status !== 'ready' ? ' · disconnected' : ''}</div></div></div>
                <button className="link" onClick={() => go('outreach/leads')}>Open leads →</button>
              </div>
              <div className="thread-body">
                {!thread && <div className="skeleton" style={{ height: 120 }} />}
                {thread?.messages.map((m) => (
                  <div key={m.id} className={`bubble ${m.direction}`}>{m.mediaKind && /^\[[^\]]+\]$/.test(m.body.trim()) ? <div className="media-ph"><KindIcon kind={m.mediaKind === 'video' ? 'video' : m.mediaKind === 'document' ? 'document' : 'image'} size={18} /><span>They sent {MEDIA_WORD[m.mediaKind] || 'a file'}. Open WhatsApp on your phone to see it.</span></div> : <div>{m.body}</div>}<AttachmentStrip items={m.attachments} linkUrl={m.linkUrl} /><small>{m.kind !== 'reply' && m.direction === 'out' ? `${m.kind} · ` : ''}{new Date(m.at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</small></div>
                ))}
                {thread && !thread.messages.length && <p className="muted" style={{ textAlign: 'center' }}>No messages yet.</p>}
                {thread?.drafts.filter((d) => ['pending', 'approved', 'sending', 'failed'].includes(d.status)).map((d) => (
                  <div key={d.id} className="thread-draft"><DraftCard d={d} canEdit={canEdit} dry={dry} act={act} approve={approve} compact /></div>
                ))}
                <div ref={end} />
              </div>
              {canEdit ? (
                <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
                  <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send(); }} placeholder={dry ? 'Dry-run is ON: replies cannot be sent yet' : 'Write a reply…  (Ctrl+Enter to send)'} aria-label="Reply" maxLength={4000} />
                  <button className="primary" disabled={sending || !text.trim() || dry}>{sending ? 'Sending…' : 'Send'}</button>
                </form>
              ) : <p className="muted" style={{ margin: 0 }}>Viewers can read conversations but not reply.</p>}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
