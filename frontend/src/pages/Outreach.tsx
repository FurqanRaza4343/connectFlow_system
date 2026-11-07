import { useState } from 'react';
import { api, Me } from '../api';
import { usePoll } from '../components/util';
import { Approvals } from './Approvals';
import { Assets } from './Assets';
import { Inbox } from './Inbox';
import { Leads } from './Leads';
import { OutreachSetup } from './OutreachSetup';

export type OutreachTab = 'leads' | 'approvals' | 'inbox' | 'assets' | 'setup';
export const OUTREACH_TABS: OutreachTab[] = ['leads', 'approvals', 'inbox', 'assets', 'setup'];

export function Outreach({ me, sub, onSub, go }: { me: Me; sub: OutreachTab; onSub: (t: OutreachTab) => void; go: (hash: string) => void }) {
  const [badge, setBadge] = useState({ review: 0, needs: 0 });
  usePoll(() => {
    api<{ pendingApprovals: number; needsHuman: number }>('/api/overview').then((o) => setBadge({ review: o.pendingApprovals, needs: o.needsHuman })).catch(() => { /* badges are optional */ });
  }, 30000);
  const tabs: Array<[OutreachTab, string, number]> = [['leads', 'Leads', 0], ['approvals', 'Approvals', badge.review], ['inbox', 'Inbox', badge.needs], ['assets', 'Assets', 0], ['setup', 'Setup', 0]];
  return (
    <section>
      <h2>Outreach</h2>
      <div className="subtabs" role="tablist">
        {tabs.map(([id, label, n]) => (
          <button key={id} role="tab" aria-selected={sub === id} className={sub === id ? 'on' : ''} onClick={() => onSub(id)}>{label}{n > 0 && <span className="tabdot">{n > 99 ? '99+' : n}</span>}</button>
        ))}
      </div>
      {sub === 'leads' && <Leads me={me} />}
      {sub === 'approvals' && <Approvals me={me} go={go} />}
      {sub === 'inbox' && <Inbox me={me} go={go} />}
      {sub === 'assets' && <Assets me={me} go={go} />}
      {sub === 'setup' && <OutreachSetup me={me} go={go} />}
    </section>
  );
}
