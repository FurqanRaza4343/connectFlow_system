import { BarChart, ErrorBox, HBars, Kpi, Loading, num, useGet } from './ui';

type O = {
  kpis: { customers: number; activeCustomers7d: number; users: number; verifiedUsers: number; numbers: number; numbersConnected: number; messagesToday: number; messages7d: number; leads: number; pendingRequests: number; mrrUsd: number };
  signups: { day: string; count: number }[]; messages: { day: string; count: number }[]; planBreakdown: { plan: string; count: number }[];
};

export function Overview({ go }: { go: (page: string) => void }) {
  const { data, err, loading } = useGet<O>('/api/admin/overview');
  if (loading && !data) return <Loading />;
  const k = data?.kpis;
  return (
    <div>
      <div className="ad-head"><div><h2>Overview</h2><p>The whole platform at a glance.</p></div></div>
      <ErrorBox msg={err} />
      {k && data && (
        <div className="ad-grid">
          <div className="ad-grid ad-kpis">
            <Kpi v={num(k.customers)} l="Customers" s={`${num(k.activeCustomers7d)} active in 7 days`} />
            <Kpi v={num(k.users)} l="Users" s={`${num(k.verifiedUsers)} confirmed`} />
            <Kpi v={`${num(k.numbersConnected)} / ${num(k.numbers)}`} l="Numbers connected" />
            <Kpi v={num(k.messagesToday)} l="Messages today" s={`${num(k.messages7d)} in 7 days`} />
            <Kpi v={num(k.leads)} l="Leads stored" />
            <Kpi v={`$${num(k.mrrUsd)}`} l="Monthly revenue (plans)" s="list prices, approved plans" />
            <div className="ad-kpi" style={{ cursor: 'pointer', borderColor: k.pendingRequests ? 'var(--warn)' : undefined }} onClick={() => go('requests')}><div className="v">{num(k.pendingRequests)}</div><div className="l">Pending plan requests</div><div className="s">open the queue →</div></div>
          </div>
          <div className="ad-grid ad-2">
            <div className="ad-card"><h3>Sign-ups · 30 days</h3><BarChart data={data.signups} label="Sign-ups per day" /></div>
            <div className="ad-card"><h3>Messages · 30 days</h3><BarChart data={data.messages} label="Messages per day" /></div>
          </div>
          <div className="ad-card" style={{ maxWidth: 520 }}><h3>Customers by plan</h3><HBars rows={data.planBreakdown.map((p) => ({ label: p.plan, value: p.count }))} /></div>
        </div>
      )}
    </div>
  );
}
