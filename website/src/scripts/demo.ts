// Interactive demo: every business and message below is fictional and nothing is sent anywhere.
type Tone = 'friendly' | 'formal' | 'short';
interface Lead {
  company: string; place: string; contact: string; site: string; signal: string; pain: string; angle: string;
  msgs: Record<Tone, string[]>;
}

const LEADS: Lead[] = [
  {
    company: 'Northwind Dental', place: 'Lahore · Dental clinic', contact: 'Sara', site: 'northwind-dental.example',
    signal: 'Hiring a front-desk lead', pain: 'No-shows, manual reminders', angle: 'WhatsApp appointment reminders',
    msgs: {
      friendly: [
        'Hi Sara, saw Northwind Dental is hiring a front-desk lead. Growing clinics often lose revenue to no-shows, and we set up WhatsApp reminders that cut them. Worth a 2-minute look this week?',
        'Hi Sara! Noticed you’re growing the front-desk team at Northwind. If missed appointments are eating into your day, we have a simple WhatsApp reminder flow clinics like yours use. Happy to show you?',
      ],
      formal: [
        'Good afternoon Sara, I noticed Northwind Dental is recruiting a front-desk lead. We help clinics reduce missed appointments through automated WhatsApp reminders. May I share a brief overview?',
        'Dear Sara, congratulations on expanding the Northwind team. Our WhatsApp reminder service helps practices lower no-show rates. Would you be open to a short conversation this week?',
      ],
      short: [
        'Hi Sara, Northwind is hiring a front-desk lead. We cut clinic no-shows with WhatsApp reminders. 2 minutes this week?',
        'Hi Sara, fewer no-shows at Northwind with WhatsApp reminders. Want a quick look?',
      ],
    },
  },
  {
    company: 'Orbit Realty', place: 'Dubai · Real estate', contact: 'Omar', site: 'orbit-realty.example',
    signal: '14 new listings this month', pain: 'Slow follow-up on enquiries', angle: 'Instant WhatsApp replies to enquiries',
    msgs: {
      friendly: [
        'Hi Omar, congrats on 14 new listings at Orbit this month. With that many enquiries, quick replies win deals. We help agencies answer and qualify buyers on WhatsApp in minutes. Open to a quick chat?',
        'Hi Omar! Orbit’s listings are growing fast. Do enquiries ever sit unanswered over a busy weekend? We help teams reply and qualify on WhatsApp straight away. Want to see how?',
      ],
      formal: [
        'Good morning Omar, I noticed Orbit Realty added fourteen listings this month. We assist agencies in responding to and qualifying enquiries promptly via WhatsApp. May I send a short summary?',
        'Dear Omar, as Orbit’s portfolio expands, response speed becomes decisive. Our WhatsApp workflow helps your team follow up within minutes. Could we arrange a brief call?',
      ],
      short: [
        'Hi Omar, 14 new listings at Orbit! We help agents reply to buyers on WhatsApp in minutes. Quick chat?',
        'Hi Omar, faster follow-up on Orbit’s enquiries via WhatsApp. Want a 2-minute demo?',
      ],
    },
  },
  {
    company: 'Pixel Parcel', place: 'London · E-commerce', contact: 'Daniel', site: 'pixel-parcel.example',
    signal: 'Launched a new store last week', pain: 'Abandoned carts, no WhatsApp channel', angle: 'Cart-recovery over WhatsApp',
    msgs: {
      friendly: [
        'Hi Daniel, congrats on the new Pixel Parcel store! Most new shops lose a big share of carts in week one. We recover them with friendly WhatsApp nudges. Want the 3-line summary?',
        'Hi Daniel! Love the new store launch. Quick idea: a gentle WhatsApp follow-up for abandoned carts often wins back sales you’ve already paid to attract. Interested in how it works?',
      ],
      formal: [
        'Good day Daniel, congratulations on launching the new Pixel Parcel store. We help online retailers recover abandoned carts through WhatsApp. May I send a concise overview?',
        'Dear Daniel, following your store launch, cart recovery is often the quickest revenue gain. Our WhatsApp approach is simple to adopt. Would a brief call suit you?',
      ],
      short: [
        'Hi Daniel, nice new store! We win back abandoned carts over WhatsApp. Want the 3-line summary?',
        'Hi Daniel, recover abandoned carts at Pixel Parcel with WhatsApp nudges. Quick look?',
      ],
    },
  },
];

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const GAPS = ['0:42', '1:34', '2:16', '1:08', '0:57'];

export function initDemo(reduced: boolean) {
  const root = document.getElementById('demo');
  if (!root) return;
  const listEl = $('demo-leads'), kv = $('d-kv'), view = $('d-view'), edit = $<HTMLTextAreaElement>('d-edit'), state = $('d-state');
  const approve = $<HTMLButtonElement>('d-approve'), regen = $<HTMLButtonElement>('d-regen'), editBtn = $<HTMLButtonElement>('d-edit-btn');
  const queuedBox = $('d-queued'), capEl = $('d-cap'), tonesEl = $('d-tones');
  const tones = Array.from(tonesEl.querySelectorAll<HTMLButtonElement>('.tone'));

  let li = 0, tone: Tone = 'friendly', vi = 0, text = '', editing = false, token = 0, cap = 5;
  const queued = new Map<number, { text: string; gap: string }>();

  const renderList = () => {
    listEl.innerHTML = '<h4><span>Leads to review</span><span>3</span></h4>' + LEADS.map((l, i) => {
      const q = queued.has(i);
      return `<button class="lead-btn${i === li ? ' on' : ''}" role="tab" aria-selected="${i === li}" data-i="${i}"><b>${l.company}</b><span>${l.place}</span><span class="st${q ? ' q' : ''}">${q ? '● Queued' : '○ Needs approval'}</span></button>`;
    }).join('');
    listEl.querySelectorAll<HTMLButtonElement>('.lead-btn').forEach((b) => b.addEventListener('click', () => select(Number(b.dataset.i))));
  };
  const renderKv = () => {
    const l = LEADS[li];
    kv.innerHTML = `<div><b>Business</b><span>${l.company}</span></div><div><b>Website</b><span>${l.site}</span></div><div><b>Contact</b><span>${l.contact}</span></div><div><b>Signal</b><span>${l.signal}</span></div><div><b>Pain</b><span>${l.pain}</span></div><div><b>Angle</b><span>${l.angle}</span></div><div><b>Number</b><span>Valid WhatsApp account ✓</span></div>`;
  };
  const renderCap = () => { capEl.textContent = `Today: ${cap} / 15 first messages`; };
  const setState = (t: string, kind: '' | 'warn' | 'bad' = 'warn') => { state.textContent = t; state.className = `tag ${kind}`; };

  async function typeInto(s: string) {
    const my = ++token;
    view.hidden = false; edit.hidden = true;
    if (reduced) { view.textContent = s; return; }
    view.innerHTML = '<span class="caret"></span>';
    let i = 0;
    while (i < s.length) {
      if (my !== token) return;
      const step = 1 + Math.floor(Math.random() * 3);
      i = Math.min(s.length, i + step);
      view.textContent = s.slice(0, i);
      const c = document.createElement('span'); c.className = 'caret'; view.appendChild(c);
      await sleep(14 + Math.random() * 16);
    }
    if (my === token) view.textContent = s;
  }

  function lock(on: boolean) {
    regen.disabled = on; editBtn.disabled = on; tones.forEach((t) => (t.disabled = on));
  }

  async function generate(newTone?: Tone) {
    if (editing || queued.has(li)) return;
    const l = LEADS[li];
    if (newTone) { tone = newTone; vi = 0; } else vi = (vi + 1) % l.msgs[tone].length;
    tones.forEach((t) => t.classList.toggle('on', t.dataset.tone === tone));
    regen.innerHTML = '<span class="spinner"></span> Regenerating…';
    lock(true); approve.disabled = true; setState('drafting…');
    view.innerHTML = '<span class="muted">Drafting a new version…</span>';
    await sleep(reduced ? 0 : 650);
    text = l.msgs[tone][vi];
    await typeInto(text);
    regen.textContent = 'Regenerate'; lock(false); approve.disabled = false; setState('needs approval');
  }

  function select(i: number) {
    if (editing) saveEdit();
    li = i; tone = 'friendly'; vi = 0;
    tones.forEach((t) => t.classList.toggle('on', t.dataset.tone === tone));
    renderList(); renderKv();
    const q = queued.get(i);
    if (q) { text = q.text; showQueued(q.gap); view.hidden = false; edit.hidden = true; view.textContent = text; lock(true); setState('approved', ''); approve.disabled = true; }
    else { queuedBox.classList.remove('show'); lock(false); approve.disabled = false; text = LEADS[i].msgs[tone][0]; setState('needs approval'); typeInto(text); }
  }

  function showQueued(gap: string) {
    queuedBox.innerHTML = `✓ Approved &amp; queued — sends in about ${gap}<button id="d-undo">Undo</button><small>Random human-like gap · within today’s cap · opt-out checked</small>`;
    queuedBox.classList.add('show');
    $('d-undo').addEventListener('click', undo);
  }

  function saveEdit() {
    const v = edit.value.trim();
    if (v) text = v;
    editing = false; editBtn.textContent = 'Edit'; edit.hidden = true; view.hidden = false; view.textContent = text;
    lock(false); setState(v && v !== LEADS[li].msgs[tone][vi] ? 'edited by you' : 'needs approval', v && v !== LEADS[li].msgs[tone][vi] ? '' : 'warn');
  }

  editBtn.addEventListener('click', () => {
    if (queued.has(li)) return;
    if (!editing) {
      token++; editing = true; editBtn.textContent = 'Save';
      edit.value = text; view.hidden = true; edit.hidden = false; edit.focus();
      regen.disabled = true; tones.forEach((t) => (t.disabled = true)); setState('editing…');
    } else saveEdit();
  });
  edit.addEventListener('keydown', (e) => { if (e.key === 'Escape') { edit.value = text; saveEdit(); } });

  regen.addEventListener('click', () => generate());
  tones.forEach((t) => t.addEventListener('click', () => { if (!t.disabled) generate(t.dataset.tone as Tone); }));

  approve.addEventListener('click', () => {
    if (queued.has(li)) return;
    if (editing) saveEdit();
    if (cap >= 15) { setState('daily cap reached', 'bad'); return; }
    token++; view.textContent = text; view.hidden = false;
    const gap = GAPS[(li + cap) % GAPS.length];
    queued.set(li, { text, gap }); cap++;
    renderList(); renderCap(); showQueued(gap); lock(true); approve.disabled = true; setState('approved', '');
  });

  function undo() {
    queued.delete(li); cap--;
    queuedBox.classList.remove('show'); lock(false); approve.disabled = false; setState('needs approval');
    renderList(); renderCap();
  }

  renderList(); renderKv(); renderCap();
  // first message types out when the demo scrolls into view
  text = LEADS[0].msgs.friendly[0]; view.textContent = text;
  if (!reduced) {
    view.textContent = '';
    new IntersectionObserver((es, io) => { if (es[0].isIntersecting) { typeInto(text); io.disconnect(); } }, { threshold: 0.4 }).observe(root);
  }
}
