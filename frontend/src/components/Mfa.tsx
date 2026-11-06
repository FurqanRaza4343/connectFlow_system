import { FormEvent, useState } from 'react';
import { api } from '../api';

/** Class names so the same two-factor screens fit the customer app and the admin console. */
export type MfaCls = { input: string; button: string; ghost: string; err: string; muted: string };
export const CUSTOMER_CLS: MfaCls = { input: '', button: 'primary big', ghost: 'link', err: 'err', muted: 'muted' };
export const ADMIN_CLS: MfaCls = { input: 'ad-in', button: 'ad-btn pri', ghost: 'ad-btn sm', err: 'ad-err', muted: 'ad-muted' };

/** Second sign-in step: the 6-digit code from the authenticator app, or a recovery code. */
export function MfaStep({ token, onDone, onCancel, cls }: { token: string; onDone: (me: any) => void; onCancel: () => void; cls: MfaCls }) {
  const [recovery, setRecovery] = useState(false);
  const [code, setCode] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault(); setErr(''); setBusy(true);
    try { onDone(await api('/api/auth/mfa/verify', 'POST', { mfaToken: token, ...(recovery ? { recoveryCode: code } : { code }) })); }
    catch (x: any) { setErr(x.message); } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="mfa-form" autoComplete="off">
      <h1>Two-step verification</h1>
      <p className={cls.muted} style={{ margin: 0 }}>{recovery ? 'Enter one of your recovery codes. Each code works once.' : 'Enter the 6-digit code from your authenticator app.'}</p>
      <label>{recovery ? 'Recovery code' : 'Authentication code'}
        <input className={`${cls.input} mfa-code`} value={code} autoFocus required
          onChange={(e) => setCode(recovery ? e.target.value : e.target.value.replace(/\D/g, '').slice(0, 6))}
          inputMode={recovery ? 'text' : 'numeric'} autoComplete="one-time-code" placeholder={recovery ? 'XXXXX-XXXXX' : '123456'} maxLength={recovery ? 14 : 6} />
      </label>
      {err && <div className={cls.err} role="alert">{err}</div>}
      <button className={cls.button} disabled={busy || (recovery ? code.trim().length < 10 : code.length !== 6)}>{busy ? 'Checking…' : 'Verify'}</button>
      <div className="mfa-links">
        <button type="button" className={cls.ghost} onClick={() => { setRecovery(!recovery); setCode(''); setErr(''); }}>{recovery ? 'Use my authenticator app' : 'Use a recovery code'}</button>
        <button type="button" className={cls.ghost} onClick={onCancel}>← Back to sign in</button>
      </div>
    </form>
  );
}

/** Set up an authenticator app: QR + manual key, confirm with a code, then show the recovery codes once. */
export function MfaEnrol({ forced, onDone, onCancel, cls }: { forced?: boolean; onDone: (me?: any) => void; onCancel?: () => void; cls: MfaCls }) {
  const [step, setStep] = useState<'intro' | 'scan' | 'codes'>('intro');
  const [setup, setSetup] = useState<{ secret: string; otpauthUri: string; qrSvg: string } | null>(null);
  const [code, setCode] = useState('');
  const [codes, setCodes] = useState<string[]>([]);
  const [me, setMe] = useState<any>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  const run = async (fn: () => Promise<void>) => { setErr(''); setBusy(true); try { await fn(); } catch (x: any) { setErr(x.message); } finally { setBusy(false); } };
  const start = () => run(async () => { setSetup(await api('/api/auth/mfa/enroll/start', 'POST')); setStep('scan'); });
  const verify = (e: FormEvent) => { e.preventDefault(); run(async () => { const r = await api<{ recoveryCodes: string[]; me: any }>('/api/auth/mfa/enroll/verify', 'POST', { code }); setCodes(r.recoveryCodes); setMe(r.me); setStep('codes'); }); };
  const download = () => {
    const url = URL.createObjectURL(new Blob([`ConnectFlow recovery codes\nEach code works once. Keep them somewhere safe.\n\n${codes.join('\n')}\n`], { type: 'text/plain' }));
    const a = document.createElement('a'); a.href = url; a.download = 'connectflow-recovery-codes.txt'; a.click(); URL.revokeObjectURL(url);
  };

  if (step === 'codes') return (
    <div className="mfa-form">
      <h1>Save your recovery codes</h1>
      <p className={cls.muted} style={{ margin: 0 }}>If you lose your phone, each of these codes lets you sign in once. We show them only now.</p>
      <ul className="mfa-codes">{codes.map((c) => <li key={c}>{c}</li>)}</ul>
      <div className="mfa-links">
        <button type="button" className={cls.ghost} onClick={() => navigator.clipboard?.writeText(codes.join('\n'))}>Copy</button>
        <button type="button" className={cls.ghost} onClick={download}>Download .txt</button>
      </div>
      <label className="chk"><input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} /> I saved these codes somewhere safe</label>
      <button className={cls.button} disabled={!saved} onClick={() => onDone(me)}>Continue</button>
    </div>
  );

  if (step === 'scan' && setup) return (
    <form onSubmit={verify} className="mfa-form" autoComplete="off">
      <h1>Scan with your authenticator app</h1>
      <p className={cls.muted} style={{ margin: 0 }}>Use Google Authenticator, Microsoft Authenticator, 1Password, Authy or any TOTP app.</p>
      <div className="mfa-qr" role="img" aria-label="QR code for your authenticator app" dangerouslySetInnerHTML={{ __html: setup.qrSvg }} />
      <details><summary className={cls.muted}>Can’t scan? Enter the key manually</summary><code className="mfa-key">{setup.secret}</code></details>
      <label>6-digit code from the app
        <input className={`${cls.input} mfa-code`} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} inputMode="numeric" autoComplete="one-time-code" placeholder="123456" maxLength={6} autoFocus required />
      </label>
      {err && <div className={cls.err} role="alert">{err}</div>}
      <button className={cls.button} disabled={busy || code.length !== 6}>{busy ? 'Checking…' : 'Turn on two-step verification'}</button>
      {onCancel && !forced && <button type="button" className={cls.ghost} onClick={onCancel}>Cancel</button>}
    </form>
  );

  return (
    <div className="mfa-form">
      <h1>{forced ? 'Set up two-step verification' : 'Protect your account'}</h1>
      <p className={cls.muted} style={{ margin: 0 }}>{forced ? 'Administrators must use two-step verification before continuing.' : 'Add a second step to sign-in with an authenticator app. A stolen password alone will no longer be enough.'}</p>
      {err && <div className={cls.err} role="alert">{err}</div>}
      <button className={cls.button} disabled={busy} onClick={start}>{busy ? 'Preparing…' : 'Set up'}</button>
      {onCancel && !forced && <button type="button" className={cls.ghost} onClick={onCancel}>Not now</button>}
    </div>
  );
}
