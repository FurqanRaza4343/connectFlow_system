import '../onboarding.css';

type Trial = { endsAt: string; daysLeft: number; expired: boolean };

export function TrialBanner({ trial, canUpgrade, onUpgrade }: { trial: Trial; canUpgrade: boolean; onUpgrade: () => void }) {
  const n = trial.daysLeft;
  const text = trial.expired
    ? 'Your 14-day trial has ended. Your data is safe; choose a plan to keep sending messages.'
    : n <= 0 ? 'Free trial: ends today' : `Free trial: ${n} ${n === 1 ? 'day' : 'days'} left`;
  return (
    <div className={`banner trial-banner${trial.expired ? ' expired' : ''}`} role="status">
      <span>{text}</span>
      {canUpgrade && <button className="primary" onClick={onUpgrade}>Upgrade</button>}
    </div>
  );
}
