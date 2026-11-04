import { useCallback, useEffect, useRef, useState } from 'react';
import { api, Instance, Me, Via, viaText } from '../api';
import { Modal } from '../components/Modal';

type Lead = {
  id: string; listId: string | null; company: string; contactName: string; role: string; businessType: string; address: string; phoneRaw: string; whatsapp: string;
  country: string; website: string; email: string; linkedin: string; facebook: string; notes: string; status: string; research: string; firstMessage: string; lastReply: string; createdAt: string;
  enriched?: boolean; via?: Via; instanceId?: string | null; sentAt?: string | null; followupStage?: number;
};
type Thread = { messages: { id: string; direction: 'in' | 'out'; body: string; kind: string; at: string }[]; drafts: { id: string; body: string; status: string; kind: string }[] };
type LeadsRes = { rows: Lead[]; total: number; page: number; pages: number; counts: Record<string, number>; withNumber: number };
type List = { id: string; name: string; count: number };
type Analyze = { headers: string[]; rowCount: number; sample: Record<string, string>[]; mapping: Record<string, string | null>; fields: string[] };

const FIELD_LABEL: Record<string, string> = {
  company: 'Company', contact_name: 'Contact person', role: 'Role / job title', business_type: 'Business type', address: 'Address', phone: 'Phone / WhatsApp',
  website: 'Website', email: 'Email', linkedin: 'LinkedIn', facebook: 'Facebook', notes: 'Notes', research: 'Research notes',
};
const STATUS_TONE: Record<string, string> = { new: 'warn', verify: 'warn', hold: 'warn', queued: 'ok', contacted: 'ok', replied: 'ok', stop: 'bad', no_whatsapp: 'bad', failed: 'bad' };
const COUNTRY: Record<string, string> = { PK: 'Pakistan', IN: 'India', AE: 'UAE', SA: 'Saudi Arabia', GB: 'United Kingdom', US: 'United States', CA: 'Canada', AU: 'Australia', DE: 'Germany', TR: 'Türkiye', BD: 'Bangladesh', EG: 'Egypt', NG: 'Nigeria', ZA: 'South Africa', ID: 'Indonesia', MY: 'Malaysia', SG: 'Singapore', QA: 'Qatar', KW: 'Kuwait', OM: 'Oman', BH: 'Bahrain' };

export function Leads({ me }: { me: Me }) {
  const [res, setRes] = useState<LeadsRes | null>(null);
  const [lists, setLists] = useState<List[]>([]);
  const [f, setF] = useState({ q: '', status: '', list: '', has: '', inst: '', page: 1 });
  const [nums, setNums] = useState<Instance[]>([]);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [edit, setEdit] = useState<Partial<Lead> | null>(null);
  const [imp, setImp] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [enr, setEnr] = useState<{ running: boolean; done: number; total: number } | null>(null);
  const [rowBusy, setRowBusy] = useState('');
  const canEdit = ['owner', 'admin', 'operator'].includes(me.role);

  const load = useCallback(async () => {
    try {
      const qs = new URLSearchParams({ q: f.q, status: f.status, list_id: f.list, has_number: f.has, instance_id: f.inst, page: String(f.page) });
      setRes(await api<LeadsRes>(`/api/leads?${qs}`)); setLists(await api<List[]>('/api/lead-lists')); setNums(await api<Instance[]>('/api/instances'));
    } catch (e: any) { setErr(e.message); }
  }, [f]);
  useEffect(() => { const t = setTimeout(load, f.q ? 250 : 0); return () => clearTimeout(t); }, [load]);

  // "Improve data" runs in the background: poll its progress while it is running, then refresh the table
  useEffect(() => {
    if (!enr?.running) return;
    const t = setInterval(async () => {
      try { const st = await api<{ running: boolean; done: number; total: number }>('/api/leads/enrich/status'); setEnr(st); if (!st.running) { setMsg(`Improved data for ${st.done} lead(s).`); load(); } } catch { /* keep polling */ }
    }, 2500);
    return () => clearInterval(t);
  }, [enr?.running, load]);
  useEffect(() => { api<{ running: boolean; done: number; total: number }>('/api/leads/enrich/status').then((st) => st.running && setEnr(st)).catch(() => { /* optional */ }); }, []);
  const improve = async (body: { ids?: string[]; all?: boolean }) => {
    setErr(''); setMsg('');
    try { const r = await api<{ queued: number }>('/api/leads/enrich', 'POST', body); setMsg(`Improving ${r.queued} lead(s) in the background: websites, contacts and socials.`); setEnr({ running: r.queued > 0, done: 0, total: r.queued }); setSel(new Set()); }
    catch (e: any) { setErr(e.message); }
  };
  const rowAct = async (id: string, what: 'research' | 'draft') => {
    setErr(''); setMsg(''); setRowBusy(`${what}:${id}`);
    try {
      await api(`/api/leads/${id}/${what}`, 'POST');
      setMsg(what === 'draft' ? 'Draft written. Review it in the Approvals tab.' : 'Research saved on the lead.'); load();
    } catch (e: any) { setErr(e.message); } finally { setRowBusy(''); }
  };
  const act = async (fn: () => Promise<unknown>, ok = '') => { setErr(''); setMsg(''); try { await fn(); setMsg(ok); setSel(new Set()); load(); } catch (e: any) { setErr(e.message); } };
  const toggle = (id: string) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const set = (k: keyof typeof f, v: string | number) => setF((o) => ({ ...o, [k]: v, page: k === 'page' ? Number(v) : 1 }));
  const c = res?.counts || {};
  const total = Object.values(c).reduce((a, b) => a + b, 0);

  return (
    <div>
      <div className="row between">
        <div><h3 style={{ margin: 0 }}>Leads</h3><p className="muted" style={{ margin: '4px 0 0' }}>{total} leads · {res?.withNumber ?? 0} with a valid WhatsApp number</p></div>
        <div className="row">
          <a className="btn-like" href={`/api/leads-export.csv?status=${f.status}&list_id=${f.list}`}>Export CSV</a>
          {canEdit && <button disabled={!!enr?.running} onClick={() => improve({ all: true })} title="Find websites, emails and social links for leads that are missing them">{enr?.running ? `Improving ${enr.done}/${enr.total}…` : 'Improve data'}</button>}
          {canEdit && <><button onClick={() => setImp(true)}>Import CSV</button><button className="primary" onClick={() => setEdit({})}>+ Add lead</button></>}
        </div>
      </div>
      {err && <div className="err">{err}</div>}{msg && <div className="okbox">{msg}</div>}

      <div className="chips">
        <button className={`chipb${!f.status ? ' on' : ''}`} onClick={() => set('status', '')}>All <b>{total}</b></button>
        {['new', 'verify', 'hold', 'queued', 'contacted', 'replied', 'stop', 'no_whatsapp', 'failed'].filter((s) => c[s]).map((s) => (
          <button key={s} className={`chipb${f.status === s ? ' on' : ''}`} onClick={() => set('status', s)}>{s.replace('_', ' ')} <b>{c[s]}</b></button>
        ))}
      </div>
      <div className="row" style={{ margin: '12px 0' }}>
        <input style={{ flex: 1, minWidth: 220 }} placeholder="Search company, person, number, website…" value={f.q} onChange={(e) => set('q', e.target.value)} />
        <select value={f.list} onChange={(e) => set('list', e.target.value)} aria-label="List"><option value="">All lists</option>{lists.map((l) => <option key={l.id} value={l.id}>{l.name} ({l.count})</option>)}</select>
        <select value={f.has} onChange={(e) => set('has', e.target.value)} aria-label="Number"><option value="">Any number</option><option value="yes">Has WhatsApp number</option><option value="no">No valid number</option></select>
        {nums.length > 1 && <select value={f.inst} onChange={(e) => set('inst', e.target.value)} aria-label="Sent from number"><option value="">All sending numbers</option>{nums.map((n) => <option key={n.id} value={n.id}>{n.label}{n.phone ? ` · +${n.phone}` : ''}</option>)}</select>}
      </div>

      {sel.size > 0 && canEdit && (
        <div className="bulk"><b>{sel.size} selected</b>
          <button disabled={!!enr?.running} onClick={() => improve({ ids: [...sel] })}>Improve data</button>
          <button onClick={() => act(() => api('/api/leads/bulk', 'POST', { ids: [...sel], action: 'hold' }), 'Put on hold.')}>Hold</button>
          <button onClick={() => act(() => api('/api/leads/bulk', 'POST', { ids: [...sel], action: 'release' }), 'Released.')}>Release</button>
          <button className="danger" onClick={() => confirm(`Delete ${sel.size} lead(s)? Contacted leads are kept.`) && act(() => api('/api/leads/bulk', 'POST', { ids: [...sel], action: 'delete' }), 'Deleted.')}>Delete</button>
        </div>
      )}

      <div className="card table-wrap">
        <table>
          <thead><tr>{canEdit && <th style={{ width: 30 }}><input type="checkbox" aria-label="Select all" checked={!!res?.rows.length && res.rows.every((r) => sel.has(r.id))} onChange={(e) => setSel(e.target.checked ? new Set(res!.rows.map((r) => r.id)) : new Set())} /></th>}<th>Company</th><th>WhatsApp</th><th>Website</th><th>Status</th><th>Number</th><th>Added</th>{canEdit && <th></th>}</tr></thead>
          <tbody>
            {res?.rows.map((l) => (
              <tr key={l.id} className="rowlink" onClick={() => canEdit && setEdit(l)}>
                {canEdit && <td onClick={(e) => e.stopPropagation()}><input type="checkbox" checked={sel.has(l.id)} onChange={() => toggle(l.id)} aria-label={`Select ${l.company}`} /></td>}
                <td><b>{l.company}</b><div className="muted" style={{ fontSize: 12.5 }}>{[l.contactName, l.role].filter(Boolean).join(' · ') || l.businessType || '—'}</div></td>
                <td>{l.whatsapp ? <>+{l.whatsapp} <span className="muted">{l.country}</span></> : <span className="muted">no valid number</span>}</td>
                <td className="muted">{l.website ? l.website.replace(/^https?:\/\//, '').slice(0, 28) : '—'}</td>
                <td><span className={`badge ${STATUS_TONE[l.status] || 'warn'}`}>{l.status.replace('_', ' ')}</span></td>
                <td className="muted">{l.via ? viaText(l.via) : '—'}</td>
                <td className="muted">{new Date(l.createdAt).toLocaleDateString()}</td>
                {canEdit && (
                  <td onClick={(e) => e.stopPropagation()}>
                    <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                      <button disabled={rowBusy === `research:${l.id}`} onClick={() => rowAct(l.id, 'research')} title="Research this business now">{rowBusy === `research:${l.id}` ? '…' : 'Research'}</button>
                      {l.whatsapp && ['new', 'hold'].includes(l.status) && <button className="primary" disabled={rowBusy === `draft:${l.id}`} onClick={() => rowAct(l.id, 'draft')} title="Write the first message now">{rowBusy === `draft:${l.id}` ? '…' : 'Draft'}</button>}
                    </div>
                  </td>
                )}
              </tr>
            ))}
            {res && !res.rows.length && <tr><td colSpan={8} className="muted" style={{ padding: 28, textAlign: 'center' }}>{total ? 'No leads match these filters.' : 'No leads yet. Import a CSV or add your first lead.'}</td></tr>}
          </tbody>
        </table>
      </div>
      {res && res.pages > 1 && <div className="row" style={{ justifyContent: 'center', marginTop: 12 }}><button disabled={f.page <= 1} onClick={() => set('page', f.page - 1)}>← Prev</button><span className="muted">Page {res.page} of {res.pages}</span><button disabled={f.page >= res.pages} onClick={() => set('page', f.page + 1)}>Next →</button></div>}

      {lists.length > 0 && canEdit && (
        <details style={{ marginTop: 20 }}><summary className="muted" style={{ cursor: 'pointer' }}>Manage lists</summary>
          <div className="card table-wrap" style={{ marginTop: 8 }}><table><tbody>
            {lists.map((l) => (
              <tr key={l.id}><td>{l.name}</td><td className="muted">{l.count} leads</td><td className="row" style={{ justifyContent: 'flex-end' }}>
                <button onClick={() => { const n = prompt('Rename list', l.name); n && act(() => api(`/api/lead-lists/${l.id}`, 'PATCH', { name: n })); }}>Rename</button>
                <button className="danger" onClick={() => confirm(`Delete the list "${l.name}"? Leads you have not messaged yet are deleted too; contacted leads are kept.`) && act(() => api(`/api/lead-lists/${l.id}?delete_leads=true`, 'DELETE'), 'List deleted.')}>Delete</button></td></tr>
            ))}</tbody></table></div></details>
      )}

      {edit && <LeadDialog lead={edit} onClose={() => setEdit(null)} onSave={async (d) => { await act(() => (edit.id ? api(`/api/leads/${edit.id}`, 'PATCH', d) : api('/api/leads', 'POST', d)), edit.id ? 'Saved.' : 'Lead added.'); setEdit(null); }} onDelete={edit.id ? async () => { if (confirm('Delete this lead?')) { await act(() => api(`/api/leads/${edit.id}`, 'DELETE'), 'Deleted.'); setEdit(null); } } : undefined} />}
      {imp && <ImportDialog onClose={() => setImp(false)} onDone={(m) => { setMsg(m); setImp(false); load(); }} />}
    </div>
  );
}

function LeadDialog({ lead, onClose, onSave, onDelete }: { lead: Partial<Lead>; onClose: () => void; onSave: (d: any) => Promise<void>; onDelete?: () => void }) {
  const [d, setD] = useState({ company: lead.company || '', contact_name: lead.contactName || '', role: lead.role || '', business_type: lead.businessType || '', address: lead.address || '', phone_raw: lead.phoneRaw || lead.whatsapp || '', website: lead.website || '', email: lead.email || '', notes: lead.notes || '' });
  const [status, setStatus] = useState(lead.status || '');
  const [busy, setBusy] = useState(false);
  const [research, setResearch] = useState(lead.research || '');
  const [thread, setThread] = useState<Thread | null>(null);
  const [note, setNote] = useState('');
  const [working, setWorking] = useState('');
  useEffect(() => { if (lead.id) api<Thread>(`/api/leads/${lead.id}/thread`).then(setThread).catch(() => setThread(null)); }, [lead.id]);
  async function run(what: 'research' | 'draft') {
    setWorking(what); setNote('');
    try {
      const r = await api<any>(`/api/leads/${lead.id}/${what}`, 'POST');
      if (what === 'research') { setResearch(r.research || ''); setNote('Research updated.'); } else setNote('Draft written. Review it in the Approvals tab.');
    } catch (e: any) { setNote(e.message); } finally { setWorking(''); }
  }
  const in_ = (k: keyof typeof d, label: string, ph = '') => <label>{label}<input value={d[k]} placeholder={ph} onChange={(e) => setD({ ...d, [k]: e.target.value })} /></label>;
  return (
    <Modal title={lead.id ? 'Edit lead' : 'Add a lead'} onClose={onClose} wide>
      <form className="stack" onSubmit={async (e) => { e.preventDefault(); setBusy(true); await onSave(lead.id ? { ...d, status: ['new', 'hold', 'verify'].includes(status) ? status : undefined } : d); setBusy(false); }}>
        <div className="two">{in_('company', 'Company *')}{in_('contact_name', 'Contact person')}</div>
        <div className="two">{in_('phone_raw', 'WhatsApp / phone', '+92 300 1234567')}{in_('website', 'Website', 'example.com')}</div>
        <div className="two">{in_('email', 'Email')}{in_('role', 'Role / job title')}</div>
        <div className="two">{in_('business_type', 'Business type')}{in_('address', 'Address')}</div>
        <label>Notes<textarea rows={3} value={d.notes} onChange={(e) => setD({ ...d, notes: e.target.value })} /></label>
        {lead.id && ['new', 'hold', 'verify'].includes(lead.status || '') && <label>Status<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="new">new</option><option value="hold">hold</option><option value="verify">verify</option></select></label>}
        {lead.id && (
          <div className="panel2">
            <div className="row between"><b>Research {lead.enriched ? <span className="badge ok">data improved</span> : null}</b>
              <span className="row" style={{ gap: 6 }}><button type="button" disabled={!!working} onClick={() => run('research')}>{working === 'research' ? 'Researching…' : 'Research now'}</button>
                {lead.whatsapp && ['new', 'hold'].includes(lead.status || '') && <button type="button" className="primary" disabled={!!working} onClick={() => run('draft')}>{working === 'draft' ? 'Writing…' : 'Draft message'}</button>}</span></div>
            <p>{research || 'Nothing researched yet. “Research now” reads their website and public info.'}</p>
            {note && <small className="muted">{note}</small>}
          </div>
        )}
        {lead.firstMessage && <div className="panel2"><b>Message sent</b><p>{lead.firstMessage}</p></div>}
        {lead.lastReply && <div className="panel2"><b>Last reply</b><p>{lead.lastReply}</p></div>}
        {thread && thread.messages.length > 0 && (
          <div className="panel2"><b>Conversation</b>
            <div className="thread-mini">{thread.messages.slice(-5).map((m) => <div key={m.id} className={`bubble ${m.direction}`}><div>{m.body.slice(0, 220)}</div><small>{new Date(m.at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</small></div>)}</div>
          </div>
        )}
        <div className="row between">{onDelete ? <button type="button" className="danger" onClick={onDelete}>Delete</button> : <span />}<button className="primary" disabled={busy || !d.company.trim()}>{busy ? 'Saving…' : 'Save'}</button></div>
      </form>
    </Modal>
  );
}

function ImportDialog({ onClose, onDone }: { onClose: () => void; onDone: (m: string) => void }) {
  const [csv, setCsv] = useState('');
  const [name, setName] = useState('');
  const [an, setAn] = useState<Analyze | null>(null);
  const [map, setMap] = useState<Record<string, string | null>>({});
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);

  async function pick(f?: File) {
    if (!f) return;
    setErr('');
    if (f.size > 5 * 1024 * 1024) return setErr('The file is larger than 5 MB.');
    const text = await f.text();
    setCsv(text); setName(f.name.replace(/\.[^.]+$/, ''));
    try { const a = await api<Analyze>('/api/leads/import/analyze', 'POST', { csv: text }); setAn(a); setMap(a.mapping); } catch (e: any) { setErr(e.message); setAn(null); }
  }
  async function run() {
    setBusy(true); setErr('');
    try {
      const r = await api<{ added: number; duplicates: number; skipped: number; withoutNumber: number }>('/api/leads/import', 'POST', { csv, mapping: map, list_name: name });
      onDone(`Imported ${r.added} lead(s)${r.duplicates ? `, ${r.duplicates} duplicate(s) skipped` : ''}${r.skipped ? `, ${r.skipped} empty row(s) skipped` : ''}${r.withoutNumber ? `. ${r.withoutNumber} have no valid WhatsApp number (marked “verify”)` : ''}.`);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <Modal title="Import leads from CSV" onClose={onClose} wide>
      <input ref={file} type="file" accept=".csv,text/csv,text/plain" hidden onChange={(e) => pick(e.target.files?.[0])} />
      {!an && <div className="drop" onClick={() => file.current?.click()}><b>Choose a CSV file</b><span className="muted">Any column names work; you’ll confirm the mapping next. Up to 5,000 rows.</span></div>}
      {err && <div className="err">{err}</div>}
      {an && (
        <div className="stack">
          <p className="muted" style={{ margin: 0 }}>{an.rowCount} rows found. Check which column is which — we guessed the obvious ones.</p>
          <div className="mapgrid">
            {an.headers.map((h) => (
              <div key={h} className="maprow"><div><b>{h}</b><div className="muted" style={{ fontSize: 12 }}>{an.sample[0]?.[h]?.slice(0, 40) || '—'}</div></div><span>→</span>
                <select value={map[h] || ''} onChange={(e) => setMap({ ...map, [h]: e.target.value || null })}><option value="">Ignore</option>{an.fields.map((x) => <option key={x} value={x}>{FIELD_LABEL[x] || x}</option>)}</select></div>
            ))}
          </div>
          <label>List name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Dentists Lahore" /></label>
          <div className="row between"><button onClick={() => { setAn(null); setCsv(''); }}>Choose another file</button><button className="primary" disabled={busy} onClick={run}>{busy ? 'Importing…' : `Import ${an.rowCount} leads`}</button></div>
        </div>
      )}
    </Modal>
  );
}

export { COUNTRY };
