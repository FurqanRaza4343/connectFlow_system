import { FormEvent, useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { CUSTOMER_CLS, MfaEnrol } from './Mfa';

type Status = { enabled: boolean; recoveryCodesLeft: number; adminRequired: boolean };

/** Settings > Security: two-step verification (authenticator app) with recovery codes. */
export function SecurityCard({ onChange }: { onChange: () => void }) {
  const [st, setSt] = useState<Status | null>(null);
  const [mode, setMode] = useState<'idle' | 'enrol' | 'disable' | 'regen' | 'codes'>('idle');
  const [pw, setPw] = useState('');
  const [code, setCode] = useState('');
  const [codes, setCodes] = useState<string[]>([]);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api<Status>('/api/auth/mfa/status').then(setSt).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const reset = () => { setMode('idle'); setPw(''); setCode(''); setErr(''); };

  async function submit(e: FormEvent) {
    e.preventDefault(); setErr(''); setMsg(''); setBusy(true);
    const second = code.replace(/\s/g, '').length > 6 ? { recoveryCode: code } : { code };
    try {
      if (mode === 'disable') { await api('/api/auth/mfa/disable', 'POST', { password: pw, ...second }); setMsg('Two-step verification is off.'); reset(); onChange(); }
      else { const r = await api<{ recoveryCodes: string[] }>('/api/auth/mfa/recovery-codes', 'POST', { password: pw, ...second }); setCodes(r.recoveryCodes); setPw(''); setCode(''); setMode('codes'); }
      load();
    } catch (x: any) { setErr(x.message); } finally { setBusy(false); }
  }

  return (
    <div className="card stack" style={{ marginTop: 14 }}>
      <div className="row between"><b>Two-step verification</b>{st && <span className={`badge ${st.enabled ? 'ok' : 'warn'}`}>{st.enabled ? 'On' : 'Off'}</span>}</div>
      {err && <div className="err">{err}</div>}{msg && <div className="okbox">{msg}</div>}
      {mode === 'enrol' && <MfaEnrol cls={CUSTOMER_CLS} onCancel={reset} onDone={() => { reset(); setMsg('Two-step verification is on.'); load(); onChange(); }} />}
      {mode === 'codes' && (
        <>
          <p className="muted" style={{ margin: 0 }}>Your new recovery codes. The old ones no longer work. We show these only once.</p>
          <ul className="mfa-codes">{codes.map((c) => <li key={c}>{c}</li>)}</ul>
          <div className="row"><button onClick={() => navigator.clipboard?.writeText(codes.join('\n'))}>Copy</button><button className="primary" onClick={reset}>Done</button></div>
        </>
      )}
      {(mode === 'disable' || mode === 'regen') && (
        <form onSubmit={submit} className="stack" autoComplete="off">
          <p className="muted" style={{ margin: 0 }}>{mode === 'disable' ? 'Confirm with your password and a current code (or a recovery code).' : 'Confirm with your password and a current code to get a fresh set of recovery codes.'}</p>
          <label>Password<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" required /></label>
          <label>Authentication code<input className="mfa-code" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code" inputMode="numeric" placeholder="123456" required /></label>
          <div className="row"><button className={mode === 'disable' ? 'danger' : 'primary'} disabled={busy || !pw || !code}>{busy ? 'Checking…' : mode === 'disable' ? 'Turn off' : 'Generate new codes'}</button><button type="button" onClick={reset}>Cancel</button></div>
        </form>
      )}
      {mode === 'idle' && st && (
        st.enabled ? (
          <>
            <p className="muted" style={{ margin: 0 }}>Sign-in needs your password and a code from your authenticator app. You have <b>{st.recoveryCodesLeft}</b> unused recovery code(s).</p>
            <div className="row"><button onClick={() => { setMsg(''); setMode('regen'); }}>New recovery codes</button>{!st.adminRequired && <button className="danger" onClick={() => { setMsg(''); setMode('disable'); }}>Turn off</button>}</div>
            {st.adminRequired && <small className="muted">Two-step verification is required for platform administrators.</small>}
          </>
        ) : (
          <>
            <p className="muted" style={{ margin: 0 }}>Add a second step to sign-in with an authenticator app (Google Authenticator, 1Password, Authy …). Strongly recommended for owners and admins.</p>
            <div className="row"><button className="primary" onClick={() => { setMsg(''); setMode('enrol'); }}>Set up two-step verification</button></div>
          </>
        )
      )}
    </div>
  );
}

/** Settings > Data (owner only): download everything the workspace holds, or ask for the workspace to be deleted. */
export function DataCard() {
  const [info, setInfo] = useState<{ deletionRequestedAt: string | null } | null>(null);
  const [pw, setPw] = useState('');
  const [confirmDel, setConfirmDel] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api<{ deletionRequestedAt: string | null }>('/api/account').then(setInfo).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);

  async function download() {
    setErr(''); setMsg(''); setBusy(true);
    try {
      const res = await fetch('/api/account/export', { credentials: 'same-origin', headers: { 'X-Requested-With': 'ConnectFlow' } });
      if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error((j as any).detail || `Error ${res.status}`); }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement('a'); a.href = url; a.download = `connectflow-export-${new Date().toISOString().slice(0, 10)}.zip`; a.click(); URL.revokeObjectURL(url);
      setMsg('Your data export has been downloaded.');
    } catch (x: any) { setErr(x.message); } finally { setBusy(false); }
  }
  async function requestDelete(e: FormEvent) {
    e.preventDefault(); setErr(''); setMsg(''); setBusy(true);
    try { await api('/api/account/delete-request', 'POST', { password: pw }); setPw(''); setConfirmDel(false); setMsg('Request received. We will confirm by email; nothing is deleted until then.'); load(); }
    catch (x: any) { setErr(x.message); } finally { setBusy(false); }
  }
  async function cancel() { setErr(''); try { await api('/api/account/delete-request/cancel', 'POST'); setMsg('Deletion request cancelled.'); load(); } catch (x: any) { setErr(x.message); } }

  return (
    <div className="card stack" style={{ marginTop: 14 }}>
      <b>Your data</b>
      {err && <div className="err">{err}</div>}{msg && <div className="okbox">{msg}</div>}
      <p className="muted" style={{ margin: 0 }}>Download everything your workspace holds (leads, conversations, drafts, numbers, team, activity) as CSV and JSON files in one ZIP. Secrets are never included. Up to 3 downloads per day.</p>
      <div className="row"><button onClick={download} disabled={busy}>{busy ? 'Preparing…' : 'Download my data'}</button></div>
      <hr style={{ border: 0, borderTop: '1px solid var(--line)', width: '100%' }} />
      {info?.deletionRequestedAt ? (
        <>
          <p style={{ margin: 0 }}>You asked us to delete this workspace on <b>{new Date(info.deletionRequestedAt).toLocaleDateString()}</b>. Nothing has been deleted yet; we will confirm with you by email.</p>
          <div className="row"><button onClick={cancel}>Cancel the deletion request</button></div>
        </>
      ) : !confirmDel ? (
        <div className="row"><button className="danger" onClick={() => setConfirmDel(true)}>Request workspace deletion…</button></div>
      ) : (
        <form onSubmit={requestDelete} className="stack">
          <p className="muted" style={{ margin: 0 }}>This sends us a request to permanently delete the workspace and its data. Nothing is deleted immediately; we confirm with you first. Enter your password to continue.</p>
          <label>Password<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" required /></label>
          <div className="row"><button className="danger" disabled={busy || !pw}>Send deletion request</button><button type="button" onClick={() => { setConfirmDel(false); setPw(''); }}>Cancel</button></div>
        </form>
      )}
    </div>
  );
}
