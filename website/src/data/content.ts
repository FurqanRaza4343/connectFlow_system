// All marketing copy lives here so pages stay thin. No invented customers, logos or statistics.
export const icons: Record<string, string> = {
  qr: '<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h2v2h-2zM18 14h2v6h-4M14 18v2"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.2 8.2-8 9-4.8-.8-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9.5"/>',
  inbox: '<path d="M4 13l2-8h12l2 8M4 13v6h16v-6M4 13h5l1 2h4l1-2h5"/>',
  code: '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M16 5.2a3.2 3.2 0 010 5.6M18 14.3c1.8.8 3 2.6 3 4.7"/>',
  bolt: '<path d="M13 3L5 14h6l-1 7 8-11h-6z"/>',
};

export const features = [
  { icon: 'qr', title: 'Your own number, linked in a minute', body: 'Scan a QR inside ConnectFlow and your WhatsApp number is connected. Add more numbers as your team grows — each workspace is fully isolated.' },
  { icon: 'spark', title: 'AI that researches before it writes', body: 'It reads the prospect’s website and public profile, then drafts a first message that actually mentions what they do. No “Dear Sir/Madam”.' },
  { icon: 'check', title: 'Approval-first sending', body: 'Every message lands in a queue first. Edit it, regenerate it with an instruction, approve it — nothing leaves without you.' },
  { icon: 'shield', title: 'A safety engine for your number', body: 'Warm-up schedules, daily caps, random human-like gaps, number validation and instant opt-out on “STOP” — built in, not bolted on.' },
  { icon: 'inbox', title: 'Replies handled, humans in control', body: 'Replies are classified and drafted. Pricing questions and complaints are flagged “needs human” so the right person steps in.' },
  { icon: 'code', title: 'API and webhooks', body: 'Send messages and receive events from your own tools. Signed webhooks with retries, scoped keys per number. (Private beta.)' },
];

export const steps = [
  { t: 'Connect your number', d: 'Create a workspace and link a WhatsApp number with a QR code. It takes about a minute, and the number stays yours.' },
  { t: 'Bring your leads', d: 'Upload a CSV or paste a Google Sheet link. AI maps the columns for you and fills in missing websites and contact details.' },
  { t: 'AI researches and drafts', d: 'For each lead it researches the business and writes a personal first message in your voice, using your company knowledge.' },
  { t: 'You approve, we send safely', d: 'Approve in one click. Messages go out paced and capped. Replies come back to your inbox, and anyone who says STOP is never contacted again.' },
];

export const safety = [
  { t: 'Warm-up and daily caps', d: 'New numbers start low and ramp up. Daily limits are enforced on every send, even if two jobs run at once.' },
  { t: 'Human-like pacing', d: 'Random gaps between messages and active-hours windows, so traffic looks like a person, not a script.' },
  { t: 'Opt-out that actually works', d: 'STOP and similar replies end the conversation immediately and are remembered. No second message, ever.' },
  { t: 'Number validation first', d: 'We check that a number is on WhatsApp before sending, so you don’t burn reputation on dead numbers.' },
  { t: 'Approval before sending', d: 'The default is human review. Auto-send is opt-in, per workspace.' },
  { t: 'Strict tenant isolation', d: 'Your leads, messages and numbers are invisible to every other customer. Platform admins act through an audit log.' },
];

export const plans = [
  { id: 'trial', name: 'Trial', usd: '0', pkr: '0', blurb: 'Try the full flow on one number.', cta: 'Start free', items: ['1 WhatsApp number', '50 messages / day', '20 AI drafts / day', 'Approval queue', 'Email support'] },
  { id: 'starter', name: 'Starter', usd: '19', pkr: '4,999', blurb: 'For founders and small sales teams.', cta: 'Start with Starter', pop: true, items: ['3 WhatsApp numbers', '500 messages / day', 'Lead import & enrichment', 'Follow-up sequences', 'Reply inbox & handoff', 'API & webhooks (beta)'] },
  { id: 'growth', name: 'Growth', usd: '59', pkr: '14,999', blurb: 'For agencies and growing teams.', cta: 'Start with Growth', items: ['10 WhatsApp numbers', '3,000 messages / day', 'Team members & roles', 'Priority support', 'Custom company knowledge', 'Early access to new features'] },
];

export const faqs = [
  { q: 'Is ConnectFlow the official WhatsApp Business API?', a: 'No. ConnectFlow connects your WhatsApp number the same way WhatsApp Web does (a linked device). That makes setup instant and template-free, but it is not an officially sanctioned integration. Meta may restrict numbers that send unwanted messages, and we cannot promise otherwise. We are adding the official Cloud API as an option for teams that need it.' },
  { q: 'Will my number get banned?', a: 'Any tool that automates WhatsApp carries some risk, and cold messaging carries the most. ConnectFlow lowers it with warm-up, daily caps, human-like pacing, number validation, approval-first sending and instant opt-out. Message people who plausibly want to hear from you, keep it relevant, and respect STOP.' },
  { q: 'Do I need a separate phone or SIM?', a: 'We recommend a dedicated business number rather than your personal one. It is linked as a device, so the phone only needs to stay registered to that number.' },
  { q: 'What does the AI actually do?', a: 'For each lead it reads public information about the business, drafts a short personal first message, and later drafts replies. You can edit or regenerate any draft, and by default nothing is sent until you approve it.' },
  { q: 'Is my data private?', a: 'Each workspace is isolated from every other. Your leads and conversations are never used to serve other customers. See our Privacy page for details.' },
  { q: 'Can I cancel any time?', a: 'Yes. Plans are monthly, and you can export your leads and conversations before you leave.' },
];
