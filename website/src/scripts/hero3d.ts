// Hero scene: an iPhone 16 (flat aluminium frame, Dynamic Island, side buttons, Camera Control) running a WhatsApp-style chat,
// inside three thin orbit rings with "lead" nodes. The phone follows the pointer (position + tilt); rings counter-move.
// Performance: capped pixel ratio, paused off-screen / hidden tab, no external assets.
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/examples/jsm/geometries/RoundedBoxGeometry.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';

const GREEN = 0x2ed47a;

// Logical iPhone 16 screen: 393 x 852 pt, drawn at 2x.
const LW = 393, LH = 852, S = 2;
const W = LW * S, H = LH * S;

type Msg = { from: 'in' | 'out'; text: string; time: string };
const CHAT: Msg[] = [
  { from: 'out', text: 'Hi Sara, saw Northwind Dental is hiring a front-desk lead. We help clinics cut no-shows with WhatsApp reminders.', time: '10:42' },
  { from: 'in', text: 'Interesting! How does it work?', time: '10:43' },
  { from: 'out', text: 'Happy to show you. Two minutes this week?', time: '10:44' },
  { from: 'in', text: 'Yes, send details.', time: '10:44' },
];

// Material Design icon paths (24x24, Apache-2.0)
const ICON = {
  back: 'M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z',
  video: 'M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z',
  call: 'M6.62 10.79c1.44 2.83 3.76 5.14 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z',
  mic: 'M12 14c1.66 0 2.99-1.34 2.99-3L15 5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z',
  camera: 'M12 8.8a3.2 3.2 0 100 6.4 3.2 3.2 0 000-6.4zM9 2L7.17 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2h-3.17L15 2H9zm3 15c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5z',
  plus: 'M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z',
  wifi: 'M1 9l2 2c4.97-4.97 13.03-4.97 18 0l2-2C16.93 2.93 7.08 2.93 1 9zm8 8l3 3 3-3c-1.65-1.66-4.34-1.66-6 0zm-4-4l2 2c2.76-2.76 7.24-2.76 10 0l2-2C15.14 9.14 8.87 9.14 5 13z',
  ticks: 'M18 7l-1.41-1.41-6.34 6.34 1.41 1.41L18 7zm4.24-1.41L11.66 16.17 7.48 12l-1.41 1.41L11.66 19l12-12-1.42-1.41zM.41 13.41L6 19l1.41-1.41L1.83 12 .41 13.41z',
  sticker: 'M21 12c0-4.97-4.03-9-9-9s-9 4.03-9 9 4.03 9 9 9h2l7-7v-2zm-8 7.5V16c0-1.1.9-2 2-2h3.5L13 19.5z',
};

function rrect(c: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  c.beginPath();
  c.moveTo(x + r, y);
  c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r); c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r);
  c.closePath();
}
function icon(c: CanvasRenderingContext2D, d: string, x: number, y: number, size: number, color: string) {
  c.save(); c.translate(x, y); c.scale(size / 24, size / 24); c.fillStyle = color; c.fill(new Path2D(d)); c.restore();
}
function wrapText(c: CanvasRenderingContext2D, text: string, maxW: number): string[] {
  const out: string[] = []; let line = '';
  for (const w of text.split(' ')) { const t = line ? `${line} ${w}` : w; if (c.measureText(t).width > maxW && line) { out.push(line); line = w; } else line = t; }
  if (line) out.push(line);
  return out;
}

const FONT = '-apple-system, "SF Pro Text", "Helvetica Neue", Arial, system-ui, sans-serif';

function drawScreen(c: CanvasRenderingContext2D, shown: number, typing: boolean) {
  c.setTransform(S, 0, 0, S, 0, 0);
  c.clearRect(0, 0, LW, LH);
  c.save(); rrect(c, 0, 0, LW, LH, 47); c.clip();

  // wallpaper with faint doodle pattern
  c.fillStyle = '#0b141a'; c.fillRect(0, 0, LW, LH);
  c.fillStyle = 'rgba(255,255,255,0.035)';
  for (let y = 24; y < LH; y += 46) for (let x = ((y / 46) % 2 ? 18 : 40); x < LW; x += 46) { c.beginPath(); c.arc(x, y, 2.2, 0, 7); c.fill(); }

  // header (status bar area + chat header share one colour, like iOS WhatsApp)
  c.fillStyle = '#1f2c34'; c.fillRect(0, 0, LW, 112);
  c.fillStyle = 'rgba(255,255,255,0.06)'; c.fillRect(0, 111, LW, 1);

  // status bar: time, signal, wifi, battery
  c.fillStyle = '#fff'; c.font = `600 16px ${FONT}`; c.textAlign = 'center'; c.fillText('9:41', 64, 35);
  c.fillStyle = '#fff';
  for (let i = 0; i < 4; i++) { const h = 4 + i * 2.6; rrect(c, 292 + i * 5.2, 33 - h, 3.4, h, 1); c.fill(); }
  icon(c, ICON.wifi, 314, 18, 18, '#fff');
  c.strokeStyle = 'rgba(255,255,255,0.45)'; c.lineWidth = 1; rrect(c, 338, 22, 25, 12.5, 4); c.stroke();
  c.fillStyle = '#fff'; rrect(c, 340, 24, 19, 8.5, 2.4); c.fill(); c.fillStyle = 'rgba(255,255,255,0.5)'; rrect(c, 364.5, 26.5, 2, 4, 1); c.fill();
  // Dynamic Island
  c.fillStyle = '#000'; rrect(c, LW / 2 - 63, 11, 126, 37, 18.5); c.fill();
  c.fillStyle = '#0d1624'; c.beginPath(); c.arc(LW / 2 + 40, 29.5, 5.5, 0, 7); c.fill();

  // chat header: back, avatar, name, online, video + voice call
  icon(c, ICON.back, 4, 60, 34, '#2ed47a');
  const g = c.createLinearGradient(40, 56, 80, 96); g.addColorStop(0, '#4adf92'); g.addColorStop(1, '#1a9e5b');
  c.fillStyle = g; c.beginPath(); c.arc(62, 82, 19, 0, 7); c.fill();
  c.fillStyle = '#04120a'; c.font = `700 18px ${FONT}`; c.fillText('S', 62, 88.5);
  c.textAlign = 'left'; c.fillStyle = '#e9edef'; c.font = `600 17px ${FONT}`; c.fillText('Sara Khan', 90, 79);
  c.fillStyle = '#8696a0'; c.font = `400 12.5px ${FONT}`; c.fillText(typing ? 'typing…' : 'online', 90, 96);
  icon(c, ICON.video, 290, 69, 26, '#e9edef');
  icon(c, ICON.call, 328, 69, 24, '#e9edef');

  // date chip
  c.font = `500 11.5px ${FONT}`; const dw = c.measureText('TODAY').width + 22;
  c.fillStyle = '#1f2c34'; rrect(c, (LW - dw) / 2, 128, dw, 24, 8); c.fill();
  c.fillStyle = '#8696a0'; c.textAlign = 'center'; c.fillText('TODAY', LW / 2, 144); c.textAlign = 'left';

  // messages (bottom-anchored above the input bar)
  c.font = `400 15.5px ${FONT}`;
  const MAXW = 252;
  const items = CHAT.slice(0, shown).map((m) => {
    const lines = wrapText(c, m.text, MAXW);
    const tw = Math.max(...lines.map((l) => c.measureText(l).width));
    return { ...m, lines, w: Math.min(MAXW + 26, Math.max(tw + 26, 84)), h: lines.length * 20 + 14 + 18 };
  });
  let y = 752;
  if (typing) {
    c.fillStyle = '#1f2c34'; rrect(c, 12, y - 38, 64, 36, 14); c.fill();
    c.fillStyle = '#8696a0'; for (let i = 0; i < 3; i++) { c.beginPath(); c.arc(32 + i * 12, y - 20, 3.4, 0, 7); c.fill(); }
    y -= 50;
  }
  for (let i = items.length - 1; i >= 0; i--) {
    const m = items[i];
    const out = m.from === 'out';
    const x = out ? LW - m.w - 12 : 12;
    y -= m.h;
    c.fillStyle = out ? '#005c4b' : '#1f2c34';
    rrect(c, x, y, m.w, m.h, 12); c.fill();
    c.beginPath(); // tail
    if (out) { c.moveTo(x + m.w - 12, y); c.lineTo(x + m.w + 5, y); c.lineTo(x + m.w - 4, y + 10); } else { c.moveTo(x + 12, y); c.lineTo(x - 5, y); c.lineTo(x + 4, y + 10); }
    c.closePath(); c.fill();
    c.fillStyle = '#e9edef'; c.font = `400 15.5px ${FONT}`;
    m.lines.forEach((l, k) => c.fillText(l, x + 12, y + 22 + k * 20));
    c.font = `400 11px ${FONT}`; c.fillStyle = out ? 'rgba(233,237,239,0.65)' : '#8696a0'; c.textAlign = 'right';
    c.fillText(m.time, x + m.w - (out ? 30 : 11), y + m.h - 8); c.textAlign = 'left';
    if (out) icon(c, ICON.ticks, x + m.w - 28, y + m.h - 20, 15, '#53bdeb');
    c.font = `400 15.5px ${FONT}`;
    y -= 6;
    if (y < 164) break;
  }

  // input bar + home indicator
  c.fillStyle = '#111b21'; c.fillRect(0, 772, LW, 80);
  icon(c, ICON.plus, 10, 784, 30, '#aebac1');
  c.fillStyle = '#1f2c34'; rrect(c, 48, 783, 252, 38, 19); c.fill();
  c.fillStyle = '#8696a0'; c.font = `400 16px ${FONT}`; c.fillText('Message', 64, 807);
  icon(c, ICON.sticker, 268, 790, 24, '#8696a0');
  icon(c, ICON.camera, 306, 786, 28, '#aebac1');
  icon(c, ICON.mic, 346, 786, 28, '#aebac1');
  c.fillStyle = '#fff'; rrect(c, LW / 2 - 67, 838, 134, 5, 2.5); c.fill();
  c.restore();
}

function dotTexture(): THREE.Texture {
  const s = 128, cv = document.createElement('canvas'); cv.width = cv.height = s;
  const c = cv.getContext('2d')!; const g = c.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
  g.addColorStop(0, 'rgba(255,255,255,1)'); g.addColorStop(0.25, 'rgba(255,255,255,0.9)'); g.addColorStop(0.5, 'rgba(255,255,255,0.25)'); g.addColorStop(1, 'rgba(255,255,255,0)');
  c.fillStyle = g; c.fillRect(0, 0, s, s);
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; return t;
}

/** Diagonal soft streak that slides across the glass as the phone turns. */
function glareTexture(): THREE.Texture {
  const cv = document.createElement('canvas'); cv.width = 512; cv.height = 512;
  const c = cv.getContext('2d')!; const g = c.createLinearGradient(0, 0, 512, 512);
  g.addColorStop(0.30, 'rgba(255,255,255,0)'); g.addColorStop(0.42, 'rgba(255,255,255,0.20)'); g.addColorStop(0.5, 'rgba(255,255,255,0.05)'); g.addColorStop(0.58, 'rgba(255,255,255,0.14)'); g.addColorStop(0.72, 'rgba(255,255,255,0)');
  c.fillStyle = g; c.fillRect(0, 0, 512, 512);
  return new THREE.CanvasTexture(cv);
}

function roundedRectShape(w: number, h: number, r: number): THREE.Shape {
  const s = new THREE.Shape(); const x = -w / 2, y = -h / 2;
  s.moveTo(x + r, y); s.lineTo(x + w - r, y); s.absarc(x + w - r, y + r, r, -Math.PI / 2, 0, false);
  s.lineTo(x + w, y + h - r); s.absarc(x + w - r, y + h - r, r, 0, Math.PI / 2, false);
  s.lineTo(x + r, y + h); s.absarc(x + r, y + h - r, r, Math.PI / 2, Math.PI, false);
  s.lineTo(x, y + r); s.absarc(x + r, y + r, r, Math.PI, Math.PI * 1.5, false);
  return s;
}

export function initHero(stage: HTMLElement) {
  const canvas = document.createElement('canvas');
  let renderer: THREE.WebGLRenderer;
  try { renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' }); } catch { return; }
  const small = matchMedia('(max-width: 900px)').matches;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, small ? 1.5 : 2));
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  stage.insertBefore(canvas, stage.firstChild);

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 60);
  camera.position.set(0, 0, 9);

  scene.add(new THREE.AmbientLight(0xb8ffd6, 0.3));
  const key = new THREE.DirectionalLight(0xffffff, 1.5); key.position.set(-4, 5, 6); scene.add(key);
  const rim = new THREE.DirectionalLight(GREEN, 2.0); rim.position.set(5, -1, -3); scene.add(rim);
  const glow = new THREE.PointLight(GREEN, 8, 8); glow.position.set(0, 0, 2.4); scene.add(glow);

  const dot = dotTexture();
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: dot, color: GREEN, transparent: true, opacity: 0.3, depthWrite: false, blending: THREE.AdditiveBlending }));
  halo.scale.set(4.6, 4.6, 1); halo.position.z = -1.5; scene.add(halo);

  // ---------------- iPhone 16 ----------------
  // Proportions from Apple's spec (71.6 x 147.6 x 7.8 mm); 1 unit = 44.7 mm.
  const BW = 1.6, BH = 3.3, BD = 0.17, BEV = 0.014, CORNER = 0.25;
  const phone = new THREE.Group();

  const frameMat = new THREE.MeshPhysicalMaterial({ color: 0x86a89f, metalness: 0.92, roughness: 0.3, clearcoat: 0.25, clearcoatRoughness: 0.4, envMapIntensity: 1.35 }); // teal anodised aluminium
  const frameGeo = new THREE.ExtrudeGeometry(roundedRectShape(BW, BH, CORNER), { depth: BD - 2 * BEV, bevelEnabled: true, bevelThickness: BEV, bevelSize: BEV, bevelSegments: 3, curveSegments: 40 });
  frameGeo.translate(0, 0, -(BD - 2 * BEV) / 2);
  phone.add(new THREE.Mesh(frameGeo, frameMat));

  // black glass front plate (thin uniform bezel)
  const front = new THREE.Mesh(
    new THREE.ShapeGeometry(roundedRectShape(BW - 0.05, BH - 0.05, CORNER - 0.025), 40),
    new THREE.MeshPhysicalMaterial({ color: 0x020403, metalness: 0.1, roughness: 0.12, clearcoat: 1, clearcoatRoughness: 0.05, envMapIntensity: 1.2 }),
  );
  front.position.z = BD / 2 + 0.0006; phone.add(front);

  // screen
  const cc = document.createElement('canvas'); cc.width = W; cc.height = H;
  const cctx = cc.getContext('2d')!;
  const tex = new THREE.CanvasTexture(cc); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 8;
  const SW = 1.43, SH = SW * (LH / LW);
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(SW, SH), new THREE.MeshBasicMaterial({ map: tex, transparent: true, toneMapped: false }));
  screen.position.z = BD / 2 + 0.0014; phone.add(screen);

  // glass glare that drifts with rotation
  const glareTex = glareTexture();
  const glare = new THREE.Mesh(new THREE.PlaneGeometry(SW, SH), new THREE.MeshBasicMaterial({ map: glareTex, transparent: true, opacity: 0.32, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false }));
  glare.position.z = BD / 2 + 0.0022; phone.add(glare);

  // buttons: left = Action + volume up/down, right = power + Camera Control
  const btn = (y: number, h: number, side: -1 | 1, mat: THREE.Material = frameMat) => {
    const m = new THREE.Mesh(new RoundedBoxGeometry(0.03, h, 0.05, 2, 0.012), mat);
    m.position.set(side * (BW / 2 + 0.003), y, 0); phone.add(m);
  };
  btn(1.12, 0.09, -1); btn(0.86, 0.17, -1); btn(0.6, 0.17, -1);
  btn(0.84, 0.27, 1);
  btn(-0.6, 0.15, 1, new THREE.MeshPhysicalMaterial({ color: 0x1b2623, metalness: 0.6, roughness: 0.25, clearcoat: 0.8 })); // Camera Control (flush, glossy)
  scene.add(phone);

  // ---------------- orbit rings with travelling lead nodes ----------------
  const rings: Array<{ g: THREE.Group; r: number; speed: number; nodes: THREE.Points; phase: number[] }> = [];
  const specs = [
    { r: 1.9, rx: 1.15, rz: 0.25, op: 0.5, speed: 0.34, n: 4 },
    { r: 2.35, rx: 1.0, rz: -0.55, op: 0.3, speed: -0.24, n: 3 },
    { r: 2.8, rx: 1.3, rz: 0.9, op: 0.18, speed: 0.16, n: 3 },
  ];
  specs.forEach((sp, k) => {
    const g = new THREE.Group(); g.rotation.set(sp.rx, 0, sp.rz);
    const pts: number[] = []; for (let i = 0; i <= 160; i++) { const a = (i / 160) * Math.PI * 2; pts.push(Math.cos(a) * sp.r, Math.sin(a) * sp.r * 0.62, 0); }
    const lg = new THREE.BufferGeometry(); lg.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    g.add(new THREE.Line(lg, new THREE.LineBasicMaterial({ color: GREEN, transparent: true, opacity: sp.op, depthWrite: false })));
    const phase = Array.from({ length: sp.n }, (_, i) => (i / sp.n) * Math.PI * 2 + k);
    const ng = new THREE.BufferGeometry(); ng.setAttribute('position', new THREE.BufferAttribute(new Float32Array(sp.n * 3), 3));
    const nodes = new THREE.Points(ng, new THREE.PointsMaterial({ map: dot, color: 0xbfffd9, size: small ? 0.3 : 0.26, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
    g.add(nodes); scene.add(g); rings.push({ g, r: sp.r, speed: sp.speed, nodes, phase });
  });

  const D = small ? 30 : 60; const dpos = new Float32Array(D * 3);
  for (let i = 0; i < D; i++) { dpos[i * 3] = (Math.random() - 0.5) * 14; dpos[i * 3 + 1] = (Math.random() - 0.5) * 9; dpos[i * 3 + 2] = -2 - Math.random() * 5; }
  const dg = new THREE.BufferGeometry(); dg.setAttribute('position', new THREE.BufferAttribute(dpos, 3));
  const dust = new THREE.Points(dg, new THREE.PointsMaterial({ map: dot, color: 0x7fe8ae, size: 0.1, transparent: true, opacity: 0.5, depthWrite: false, blending: THREE.AdditiveBlending }));
  scene.add(dust);

  // ---------------- chat timeline ----------------
  let shown = 2, typing = false, lastStep = 0, dirty = true;
  const stepChat = (ms: number) => {
    if (ms - lastStep < 2400) return; lastStep = ms;
    if (typing) { typing = false; shown = Math.min(CHAT.length, shown + 1); }
    else if (shown >= CHAT.length) shown = 1;
    else if (CHAT[shown].from === 'in') typing = true;
    else shown += 1;
    dirty = true;
  };

  // ---------------- sizing, pointer, visibility ----------------
  const resize = () => {
    const width = canvas.clientWidth, height = canvas.clientHeight; if (!width || !height) return;
    renderer.setSize(width, height, false); camera.aspect = width / height;
    camera.position.z = camera.aspect < 0.9 ? 10.2 : 9; camera.updateProjectionMatrix();
  };
  resize(); new ResizeObserver(resize).observe(stage);

  const target = { x: 0, y: 0 }, cur = { x: 0, y: 0 };
  addEventListener('pointermove', (e) => { target.x = (e.clientX / innerWidth - 0.5) * 2; target.y = (e.clientY / innerHeight - 0.5) * 2; }, { passive: true });
  const touch = !matchMedia('(hover: hover)').matches;

  let visible = true;
  new IntersectionObserver((es) => { visible = es[0].isIntersecting; }, { threshold: 0 }).observe(stage);

  const clock = new THREE.Clock(); let first = true;
  const frame = () => {
    requestAnimationFrame(frame);
    if (!visible || document.hidden) return;
    const t = clock.getElapsedTime();
    if (touch) { target.x = Math.sin(t * 0.5) * 0.6; target.y = Math.cos(t * 0.4) * 0.3; }
    cur.x += (target.x - cur.x) * 0.06; cur.y += (target.y - cur.y) * 0.06;

    stepChat(t * 1000);
    if (dirty) { drawScreen(cctx, shown, typing); tex.needsUpdate = true; dirty = false; }

    const sy = Math.min(1, scrollY / 800);
    phone.rotation.y = -0.28 + cur.x * 0.5 + sy * 0.5;
    phone.rotation.x = 0.05 + cur.y * 0.24;
    phone.position.x = cur.x * 0.42;
    phone.position.y = -cur.y * 0.22 + Math.sin(t * 0.9) * 0.09;
    glareTex.offset.x = -phone.rotation.y * 0.9 + Math.sin(t * 0.3) * 0.05;
    glareTex.offset.y = phone.rotation.x * 0.7;
    glow.intensity = 7 + Math.sin(t * 1.6) * 2;
    halo.position.x = phone.position.x * 0.5; halo.position.y = phone.position.y * 0.5;

    rings.forEach((r, k) => {
      r.g.position.x = -cur.x * (0.18 + k * 0.1); r.g.position.y = cur.y * (0.1 + k * 0.06);
      r.g.rotation.z += 0.0009 * (k % 2 ? -1 : 1);
      const p = r.nodes.geometry.attributes.position as THREE.BufferAttribute;
      r.phase.forEach((ph, i) => { const a = ph + t * r.speed; p.setXYZ(i, Math.cos(a) * r.r, Math.sin(a) * r.r * 0.62, 0); });
      p.needsUpdate = true;
    });
    dust.position.x = -cur.x * 0.5; dust.position.y = cur.y * 0.3;

    renderer.render(scene, camera);
    if (first) { first = false; stage.classList.add('is-3d'); }
  };
  frame();
}
