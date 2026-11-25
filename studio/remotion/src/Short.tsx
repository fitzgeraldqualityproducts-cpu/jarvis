// Jarvis studio compositor: the scenes (Manim clips or still pictures) with a slow push-in, short
// crossfades, a punch-in on the hook, animated word-by-word captions and a seamless loop back into the
// first frame. Everything is driven by the props Jarvis writes (remotion_props.json); nothing here is
// downloaded or random, so the same props always give the same video.
import React from 'react';
import {
  AbsoluteFill, Audio, Img, OffthreadVideo, Sequence, interpolate, spring, staticFile,
  useCurrentFrame, useVideoConfig,
} from 'remotion';

type Hud = {label: string; value: string; sub?: string; num?: number; from?: number; dec?: number; unit?: string};
type Bar = {label: string; v: number; frac: number; text: string; top: boolean};
type Overlay = {kind: 'bars' | 'equation' | 'steps' | 'wave' | 'measure' | 'thermo'; x: number; y: number; w: number; h: number;
  items?: (Bar | string)[]; terms?: string[]; right?: string; note?: string; log?: boolean;
  span?: 'gap' | 'wavelength'; value?: string; spot?: string; label?: string; from?: string; to?: string;
  geo?: {peaks: number[]; nodes: number[]; a: number; b: number}; frac?: number; low?: string; high?: string;
  cold?: boolean};
type Cue = {t: number; kind: 'cut' | 'count' | 'pop' | 'rehook' | 'payoff' | 'jump'; scene: number; until?: number;
  sfx?: string; transition?: string};
type Scene = {start: number; end: number; image: string; video?: string; videoSeconds?: number; hold?: string;
  bg?: string; hud?: Hud | null; title?: string; motion?: string; overlay?: Overlay;
  shots?: {at: number; video: string}[]; transition?: 'fade' | 'zoom'};
type Word = {w: string; s: number; e: number};
type Page = {s: number; e: number; words: number[]; text?: string; hi?: number[]; reveal?: boolean};

export type ShortProps = {
  fps: number; width: number; height: number; duration: number; audio: string;
  scenes: Scene[]; words: Word[]; pages: Page[]; keywords: number[];
  palette: {cyan: string; amber: string; text: string; background: string};
  caption: {y: number; left: number; width: number; size: number; maxLines: number; lineHeight?: number};
  fade: number; punch: number; loopBlend: number;
  look?: 'diagram' | 'hud';
  type?: 'serif' | 'bold';
  fx?: Cue[];
};

export const defaults: ShortProps = {
  fps: 30, width: 1080, height: 1920, duration: 10, audio: '', scenes: [], words: [], pages: [], keywords: [],
  palette: {cyan: '#36D1FF', amber: '#FFB020', text: '#F2F5FF', background: '#0B1020'},
  caption: {y: 0.7, left: 80, width: 850, size: 74, maxLines: 2},
  fade: 0.25, punch: 0.06, loopBlend: 0.35,
};

const SceneLayer: React.FC<{scene: Scene; index: number; p: ShortProps}> = ({scene, index, p}) => {
  const frame = useCurrentFrame();               // frames since this scene's sequence began
  const t = frame / p.fps;
  const span = Math.max(0.5, scene.end - scene.start);
  let zoom = 1 + 0.035 * Math.min(1, Math.max(0, t / span));
  if (index === 0) zoom += p.punch * Math.max(0, 1 - t / 0.4);
  const opacity = index === 0 ? 1 : interpolate(t, [0, p.fade], [0, 1], {extrapolateRight: 'clamp'});
  const inVideo = scene.video && t < (scene.videoSeconds || 0) - 1 / p.fps;
  const still = scene.video ? (inVideo ? null : scene.hold || scene.image) : scene.image;
  return (
    <AbsoluteFill style={{opacity, transform: `scale(${zoom})`, transformOrigin: '50% 40%'}}>
      {inVideo ? <OffthreadVideo src={staticFile(scene.video!)} muted /> : null}
      {still ? <Img src={staticFile(still)} /> : null}
    </AbsoluteFill>
  );
};

const Captions: React.FC<{p: ShortProps}> = ({p}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const page = p.pages.find((pg) => pg.s <= t && t < pg.e);
  if (!page) return null;
  const keys = new Set(p.keywords);
  const pageIn = spring({frame: frame - Math.round(page.s * fps), fps, config: {damping: 200}, durationInFrames: 6});
  return (
    <div style={{
      position: 'absolute', left: p.caption.left, width: p.caption.width,
      top: p.caption.y * p.height, transform: `translateY(-50%) scale(${0.92 + 0.08 * pageIn})`,
      display: 'flex', flexWrap: 'wrap', justifyContent: 'center', alignItems: 'center', rowGap: 6,
      fontFamily: 'Arial Black, Arial, Helvetica, sans-serif', fontWeight: 900, fontSize: p.caption.size,
      lineHeight: 1.12, textAlign: 'center', opacity: pageIn,
    }}>
      {page.words.map((i) => {
        const w = p.words[i];
        const on = w.s <= t;
        const current = on && t < w.e + 0.05;
        const pop = spring({frame: frame - Math.round(w.s * fps), fps, config: {damping: 12, stiffness: 220}, durationInFrames: 8});
        const color = current || keys.has(i) ? p.palette.amber : on ? p.palette.text : 'rgba(242,245,255,0.55)';
        return (
          <span key={i} style={{
            color, display: 'inline-block', margin: '0 0.17em', transformOrigin: '50% 60%', transform: `scale(${current ? 1 + 0.1 * pop : 1})`,
            WebkitTextStroke: '10px rgba(5,8,20,0.92)', paintOrder: 'stroke fill',
            textShadow: '0 6px 18px rgba(0,0,0,0.55)',
          }}>{w.w}</span>
        );
      })}
    </div>
  );
};

// ---------- the cinematic look: full-screen picture, a counter top left, one serif sentence at a time ----------
const SERIF = 'Georgia, "Times New Roman", Times, serif';
// the bold type system (props.type "bold"): a heavy sans-serif with a dark outline, readable on any picture
const BLACK = '"Arial Black", "Arial Bold", Arial, Helvetica, sans-serif';
const OUTLINE = '0 0 2px #000, 0 3px 0 rgba(0,0,0,0.85), 0 0 18px rgba(0,0,0,0.6)';
const big = (bold?: boolean): React.CSSProperties => (bold ? {fontFamily: BLACK, fontWeight: 900, textShadow: OUTLINE}
  : {fontFamily: SERIF});
const TITLE_TOP = 650;                                        // same place as Jarvis's still (video.TITLE_TOP)
const SHADOW = '0 2px 4px rgba(0,0,0,0.75), 0 0 24px rgba(0,0,0,0.55)';

const fmt = (x: number, dec: number) =>
  x.toLocaleString('en-US', {minimumFractionDigits: dec, maximumFractionDigits: dec});

const HudBox: React.FC<{hud: Hud; span: number; fps: number; bold?: boolean}> = ({hud, span, fps, bold}) => {
  const frame = useCurrentFrame();
  const t = frame / fps;
  let value = hud.value;
  if (hud.num !== undefined && hud.from !== undefined && hud.from !== hud.num) {
    const k = interpolate(t, [0, Math.max(0.4, span * 0.7)], [0, 1], {extrapolateRight: 'clamp'});
    const eased = 1 - Math.pow(1 - k, 3);
    const x = hud.from + (hud.num - hud.from) * eased;
    value = k >= 1 ? hud.value : `${fmt(x, hud.dec || 0)}${hud.unit ? ' ' + hud.unit : ''}`;
  }
  return (
    <div style={{position: 'absolute', left: 90, top: 270, maxWidth: 900, color: 'white', textShadow: SHADOW,
      overflowWrap: 'break-word'}}>
      {hud.label ? <div style={{fontFamily: 'Arial, Helvetica, sans-serif', fontWeight: 700, fontSize: 24,
        letterSpacing: '0.12em', opacity: 0.92}}>{hud.label}</div> : null}
      <div style={{...big(bold), fontSize: bold ? 84 : 88, lineHeight: 1.05, marginTop: 8,
        fontVariantNumeric: 'tabular-nums'}}>{value}</div>
      {hud.sub ? <div style={{fontFamily: 'Arial, Helvetica, sans-serif', fontWeight: 700, fontSize: 22,
        letterSpacing: '0.12em', opacity: 0.85, marginTop: 10}}>{hud.sub}</div> : null}
    </div>
  );
};

// ---------- animated diagrams over the picture (edit "lively") ----------
const SANS = 'Arial, Helvetica, sans-serif';

const OverlayBox: React.FC<{ov: Overlay; span: number; p: ShortProps}> = ({ov, span, p}) => {
  const frame = useCurrentFrame();
  const fps = p.fps;
  const t = frame / fps;
  const appear = spring({frame: frame - Math.round(0.15 * fps), fps, config: {damping: 200}, durationInFrames: 10});
  const panel: React.CSSProperties = {position: 'absolute', left: ov.x, top: ov.y, width: ov.w, height: ov.h,
    boxSizing: 'border-box', padding: '26px 34px', borderRadius: 28, background: 'rgba(6,10,24,0.58)',
    border: '1px solid rgba(255,255,255,0.12)', color: 'white', opacity: appear,
    transform: `translateY(${(1 - appear) * 30}px)`, display: 'flex', flexDirection: 'column',
    justifyContent: 'center', textShadow: SHADOW};
  if (ov.kind === 'bars') {
    const bars = (ov.items || []) as Bar[];
    const row = (ov.h - 52 - (ov.note ? 30 : 0)) / Math.max(1, bars.length);
    return (
      <div style={panel}>
        {bars.map((b, i) => {
          const grow = spring({frame: frame - Math.round((0.3 + i * 0.14) * fps), fps,
            config: {damping: 18, stiffness: 90}, durationInFrames: 22});
          const full = ov.w - 68 - 210;
          return (
            <div key={i} style={{height: row, display: 'flex', flexDirection: 'column', justifyContent: 'center'}}>
              <div style={{fontFamily: SANS, fontWeight: 700, fontSize: Math.min(30, row * 0.32), letterSpacing: '0.04em',
                opacity: 0.92}}>{b.label}</div>
              <div style={{display: 'flex', alignItems: 'center', marginTop: 6}}>
                <div style={{height: Math.min(34, row * 0.34), width: Math.max(6, full * b.frac * grow), borderRadius: 10,
                  background: b.top ? p.palette.amber : p.palette.cyan, boxShadow: '0 4px 14px rgba(0,0,0,0.4)'}} />
                <div style={{...big(p.type === 'bold'), fontSize: Math.min(38, row * 0.4), marginLeft: 16, opacity: grow,
                  whiteSpace: 'nowrap'}}>{b.text}</div>
              </div>
            </div>
          );
        })}
        {ov.note ? <div style={{fontFamily: SANS, fontSize: 22, opacity: 0.7, marginTop: 6}}>{ov.note}</div> : null}
      </div>
    );
  }
  if (ov.kind === 'equation') {
    const terms = ov.terms || [];
    const step = Math.min(0.22, Math.max(0.1, (span * 0.45) / Math.max(1, terms.length + 1)));
    const rightAt = 0.3 + step * terms.length;
    const pop = spring({frame: frame - Math.round(rightAt * fps), fps, config: {damping: 10, stiffness: 160},
      durationInFrames: 14});
    return (
      <div style={{...panel, alignItems: 'center', textAlign: 'center'}}>
        <div style={{...big(p.type === 'bold'), fontSize: p.type === 'bold' ? 54 : 58, lineHeight: 1.2, display: 'flex', flexWrap: 'wrap',
          justifyContent: 'center', columnGap: 16}}>
          {terms.map((x, i) => {
            const a = interpolate(t, [0.3 + step * i, 0.3 + step * i + 0.15], [0, 1],
              {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
            return <span key={i} style={{opacity: a, transform: `translateY(${(1 - a) * 14}px)`,
              display: 'inline-block'}}>{x}</span>;
          })}
        </div>
        <div style={{...big(p.type === 'bold'), fontSize: 96, marginTop: 14, color: p.palette.amber, opacity: Math.min(1, pop * 1.4),
          transform: `scale(${0.6 + 0.4 * pop})`}}>= {ov.right}</div>
      </div>
    );
  }
  if (ov.kind === 'wave' && ov.geo) {
    // a standing wave: it swings in place, the peaks (e.g. hot spots) glow, then an arrow spans the gap
    const g = ov.geo;
    const inner = ov.w - 68;
    const svgH = ov.h - 44 - (ov.label ? 44 : 0);       // everything stays inside the panel
    const amp = Math.max(30, (svgH - 130) / 2);
    const mid = 40 + amp;
    const half = inner / Math.max(1, g.peaks.length);
    const swing = Math.cos(t * 2 * Math.PI * 1.1);
    const draw = interpolate(t, [0.15, 0.7], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
    const pts: string[] = [];
    for (let x = 0; x <= inner * draw; x += 6) {
      pts.push(`${x.toFixed(1)},${(mid - amp * swing * Math.sin(Math.PI * x / half)).toFixed(1)}`);
    }
    const ghost: string[] = [];
    for (let x = 0; x <= inner; x += 8) {
      ghost.push(`${x},${(mid + amp * Math.sin(Math.PI * x / half)).toFixed(1)}`);
    }
    const arrow = interpolate(t, [0.9, 1.4], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
    const ya = mid + amp + 30;
    return (
      <div style={{...panel, justifyContent: 'flex-start', padding: '22px 34px'}}>
        {ov.label ? <div style={{fontFamily: SANS, fontWeight: 700, fontSize: 30, letterSpacing: '0.04em'}}>{ov.label}</div> : null}
        <svg width={inner} height={svgH} style={{overflow: 'visible'}}>
          <polyline points={ghost.join(' ')} fill="none" stroke="rgba(255,255,255,0.22)" strokeWidth={3}
            strokeDasharray="8 10" opacity={draw} />
          <polyline points={pts.join(' ')} fill="none" stroke={p.palette.cyan} strokeWidth={7} strokeLinecap="round" />
          {g.nodes.map((x, i) => <circle key={`n${i}`} cx={x} cy={mid} r={6} fill="rgba(255,255,255,0.5)" opacity={draw} />)}
          {g.peaks.map((x, i) => {
            const on = interpolate(t, [0.6 + i * 0.08, 0.8 + i * 0.08], [0, 1],
              {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
            return <circle key={`p${i}`} cx={x} cy={mid} r={14 + 22 * on * (0.75 + 0.25 * Math.abs(swing))}
              fill={p.palette.amber} opacity={0.35 + 0.55 * on} />;
          })}
          <line x1={g.a} x2={g.a + (g.b - g.a) * arrow} y1={ya} y2={ya} stroke={p.palette.amber} strokeWidth={5} />
          {[g.a, g.b].map((x, i) => <line key={`t${i}`} x1={x} x2={x} y1={ya - 16} y2={ya + 16}
            stroke={p.palette.amber} strokeWidth={5} opacity={i === 0 ? arrow > 0 ? 1 : 0 : arrow >= 1 ? 1 : 0} />)}
          <text x={(g.a + g.b) / 2} y={ya + 48} textAnchor="middle" fill="white" opacity={arrow}
            style={{fontFamily: SANS, fontWeight: 800, fontSize: 40}}>{ov.value || (ov.span === 'wavelength' ? 'one wavelength' : 'half a wavelength')}</text>
          <text x={g.peaks[g.peaks.length - 1]} y={mid - amp - 18} textAnchor="middle" fill={p.palette.amber}
            opacity={draw} style={{fontFamily: SANS, fontWeight: 700, fontSize: 26}}>{ov.spot}</text>
        </svg>
      </div>
    );
  }
  if (ov.kind === 'thermo') {
    // a thermometer that fills (or drains) to the temperature, its scale marked, the value popping in beside it
    const fill = spring({frame: frame - Math.round(0.25 * fps), fps, config: {damping: 22, stiffness: 70},
      durationInFrames: 26});
    const pop = spring({frame: frame - Math.round(0.9 * fps), fps, config: {damping: 10, stiffness: 160},
      durationInFrames: 14});
    const tubeH = ov.h - 120;
    const level = tubeH * (ov.frac || 0) * fill;
    const col = ov.cold ? p.palette.cyan : p.palette.amber;
    return (
      <div style={{...panel, flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-start', gap: 40}}>
        <svg width={190} height={ov.h - 52} style={{flexShrink: 0}}>
          <rect x={44} y={10} width={32} height={tubeH} rx={16} fill="rgba(255,255,255,0.12)" stroke="white" strokeWidth={3} />
          <rect x={50} y={10 + tubeH - level} width={20} height={level} rx={10} fill={col} />
          <circle cx={60} cy={tubeH + 34} r={30} fill={col} stroke="white" strokeWidth={3} />
          {[0, 0.25, 0.5, 0.75, 1].map((f) => <line key={f} x1={78} x2={94} y1={10 + tubeH * (1 - f)} y2={10 + tubeH * (1 - f)}
            stroke="white" strokeWidth={2} opacity={0.7} />)}
          <text x={100} y={22} fill="white" opacity={0.8} style={{fontFamily: SANS, fontSize: 20}}>{ov.high}</text>
          <text x={100} y={tubeH + 8} fill="white" opacity={0.8} style={{fontFamily: SANS, fontSize: 20}}>{ov.low}</text>
        </svg>
        <div style={{display: 'flex', flexDirection: 'column'}}>
          {ov.label ? <div style={{fontFamily: SANS, fontWeight: 700, fontSize: 30, letterSpacing: '0.03em',
            maxWidth: ov.w - 260}}>{ov.label}</div> : null}
          <div style={{...big(true), fontSize: 96, color: col, opacity: Math.min(1, pop * 1.4),
            transform: `scale(${0.6 + 0.4 * pop})`, transformOrigin: 'left center', marginTop: 8}}>{ov.value}</div>
        </div>
      </div>
    );
  }
  if (ov.kind === 'measure') {
    // a ruler that grows from one thing to the other, then the distance pops in
    const inner = ov.w - 68;
    const grow = spring({frame: frame - Math.round(0.3 * fps), fps, config: {damping: 20, stiffness: 80},
      durationInFrames: 24});
    const pop = spring({frame: frame - Math.round(1.0 * fps), fps, config: {damping: 10, stiffness: 160},
      durationInFrames: 14});
    const ticks = Array.from({length: 11}, (_, i) => i);
    return (
      <div style={{...panel, alignItems: 'center', textAlign: 'center'}}>
        {ov.label ? <div style={{fontFamily: SANS, fontWeight: 700, fontSize: 30, letterSpacing: '0.04em'}}>{ov.label}</div> : null}
        <div style={{fontFamily: SANS, fontWeight: 900, fontSize: 92, color: p.palette.amber, opacity: Math.min(1, pop * 1.4),
          transform: `scale(${0.6 + 0.4 * pop})`, margin: '6px 0 10px'}}>{ov.value}</div>
        <svg width={inner} height={90}>
          <rect x={0} y={30} width={inner * grow} height={34} rx={8} fill="rgba(255,255,255,0.16)"
            stroke={p.palette.amber} strokeWidth={3} />
          {ticks.map((i) => <line key={i} x1={inner * grow * i / 10} x2={inner * grow * i / 10} y1={30}
            y2={i % 5 === 0 ? 64 : 48} stroke={p.palette.amber} strokeWidth={3} />)}
        </svg>
        <div style={{display: 'flex', justifyContent: 'space-between', width: inner, fontFamily: SANS, fontSize: 28,
          opacity: 0.85}}><span>{ov.from}</span><span style={{opacity: grow}}>{ov.to}</span></div>
      </div>
    );
  }
  const items = (ov.items || []) as string[];
  const gap = span / (items.length + 0.6);
  const lit = Math.min(items.length - 1, Math.floor(Math.max(0, t - 0.2) / gap));
  return (
    <div style={panel}>
      {items.map((x, i) => {
        const a = interpolate(t, [0.2 + gap * i, 0.2 + gap * i + 0.2], [0, 1],
          {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
        const on = i === lit;
        return (
          <div key={i} style={{display: 'flex', alignItems: 'center', margin: '10px 0', opacity: a * (on ? 1 : 0.6),
            transform: `translateX(${(1 - a) * -24}px)`}}>
            <div style={{width: 54, height: 54, borderRadius: 27, flex: '0 0 54px', display: 'flex', alignItems: 'center',
              justifyContent: 'center', fontFamily: SANS, fontWeight: 800, fontSize: 28,
              background: on ? p.palette.amber : 'rgba(255,255,255,0.14)', color: on ? '#101420' : 'white'}}>{i + 1}</div>
            <div style={{fontFamily: SANS, fontWeight: on ? 700 : 400, fontSize: 34, marginLeft: 20}}>{x}</div>
          </div>
        );
      })}
    </div>
  );
};

// the edit's cues as a camera wobble: punch-ins, a shake on the payoff, a jump zoom in long scenes, a flash
const cueLook = (fx: Cue[], scenes: Scene[], t: number) => {
  let scale = 1;
  let x = 0;
  let y = 0;
  let flash = 0;
  for (const c of fx) {
    const d = t - c.t;
    if (c.kind === 'jump') {
      const end = scenes[c.scene] ? scenes[c.scene].end : c.t;
      if (t >= c.t && t < end) {
        scale *= 1.07;
      }
      continue;
    }
    if (d < 0 || d > 0.6) {
      continue;
    }
    if (c.kind === 'pop') {
      scale *= 1 + 0.035 * Math.pow(Math.max(0, 1 - d / 0.22), 2);
    } else if (c.kind === 'payoff') {
      scale *= 1 + 0.07 * Math.pow(Math.max(0, 1 - d / 0.35), 2);
      const k = Math.max(0, 1 - d / 0.42);
      x += 14 * k * Math.sin(d * 70);
      y += 9 * k * Math.sin(d * 53 + 1.3);
    } else if (c.kind === 'rehook') {
      flash = Math.max(flash, 0.5 * Math.max(0, 1 - d / 0.18));
      scale *= 1 + 0.04 * Math.pow(Math.max(0, 1 - d / 0.3), 2);
    }
  }
  return {scale, x, y, flash};
};

const ShotLayer: React.FC<{video: string; fps: number}> = ({video, fps}) => {
  const frame = useCurrentFrame();
  const a = interpolate(frame, [0, Math.max(1, Math.round(0.12 * fps))], [0, 1], {extrapolateRight: 'clamp'});
  return <AbsoluteFill style={{opacity: a}}><OffthreadVideo src={staticFile(video)} muted /></AbsoluteFill>;
};

const CinemaScene: React.FC<{scene: Scene; index: number; p: ShortProps}> = ({scene, index, p}) => {
  const frame = useCurrentFrame();
  const t = frame / p.fps;
  const span = Math.max(0.5, scene.end - scene.start);
  const k = Math.min(1, Math.max(0, t / span));
  const zoom = 1 + 0.08 * k;                                  // a slow, steady push-in, like a moving camera
  const drift = (index % 2 === 0 ? -1 : 1) * 18 * k;
  // the storyboard's transition into this scene: a longer dissolve ("fade"), a quick zoom-in ("zoom") or a cut
  const fadeIn = scene.transition === 'fade' ? Math.max(p.fade, 0.5) : p.fade;
  const opacity = index === 0 ? 1 : interpolate(t, [0, fadeIn], [0, 1], {extrapolateRight: 'clamp'});
  const zoomIn = scene.transition === 'zoom' ? interpolate(t, [0, 0.35], [1.12, 1], {extrapolateRight: 'clamp'}) : 1;
  // text never crossfades with text: the outgoing scene's counter, card and title leave the moment the next scene
  // starts (only the pictures dissolve), and the last scene's text is gone before the loop blends back to frame 1
  // and the incoming scene's text waits until that is gone, so the two never show at once
  const textA = Math.min(
    interpolate(t, [span, span + 0.06], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}),
    index === 0 ? 1 : interpolate(t, [0.07, 0.17], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}),
    textLeft(p, scene.start + t));
  return (
    <AbsoluteFill style={{opacity, transform: zoomIn !== 1 ? `scale(${zoomIn})` : undefined}}>
      {scene.motion ? (
        // a camera move through the picture (depth) or a real clip, already cut to this scene by Jarvis; the
        // storyboard's later shots of the same moment come in on top, each with its own move
        <AbsoluteFill>
          <OffthreadVideo src={staticFile(scene.motion)} muted />
          {(scene.shots || []).map((sh, j) => {
            const from = Math.round(sh.at * p.fps);
            return (
              <Sequence key={j} from={from} layout="none">
                <ShotLayer video={sh.video} fps={p.fps} />
              </Sequence>
            );
          })}
        </AbsoluteFill>
      ) : (
        <AbsoluteFill style={{transform: `translateX(${drift}px) scale(${zoom})`,
          transformOrigin: '50% 45%'}}>
          <Img src={staticFile(scene.bg || scene.image)} />
        </AbsoluteFill>
      )}
      <AbsoluteFill style={{opacity: textA}}>
      {scene.overlay ? <OverlayBox ov={scene.overlay} span={span} p={p} /> : null}
      {scene.hud ? <HudBox hud={scene.hud} span={span} fps={p.fps} bold={p.type === 'bold'} /> : null}
      {scene.title ? (
        <div style={{position: 'absolute', left: 80, width: 850, top: TITLE_TOP, textAlign: 'center', color: 'white',
          textShadow: SHADOW, ...big(p.type === 'bold'), fontSize: p.type === 'bold' ? 68 : 66,
          lineHeight: p.type === 'bold' ? 1.12 : 1.2}}>{scene.title}</div>
      ) : null}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// 1 until just before the loop blend, then 0: the last scene's words are gone before frame 1 (with its own
// question and counter) blends back in, so two sets of text are never on screen at once
const textLeft = (p: ShortProps, gt: number) => p.scenes.length > 1 && p.loopBlend > 0
  ? interpolate(gt, [p.duration - p.loopBlend - 0.15, p.duration - p.loopBlend], [1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 1;

const LineCaption: React.FC<{p: ShortProps}> = ({p}) => {
  const frame = useCurrentFrame();
  const t = frame / p.fps;
  const page = p.pages.find((pg) => pg.s <= t && t < pg.e);
  if (!page || !page.text) return null;
  const a = textLeft(p, t) *
    interpolate(t, [page.s, page.s + 0.18], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  if (p.type === 'bold') {
    // 1-2 short lines that pop in; numbers and key words in the accent colour; the word being said lifts a
    // little; the payoff line comes in bigger, as a reveal
    const pop = spring({frame: frame - Math.round(page.s * p.fps), fps: p.fps, config: {damping: 14, stiffness: 220},
      durationInFrames: 8});
    const hi = new Set(page.hi || []);
    const toks = page.text.split(/\s+/);
    const size = p.caption.size * (page.reveal ? 1.12 : 1);
    return (
      <div style={{position: 'absolute', left: p.caption.left, width: p.caption.width, top: p.caption.y * p.height,
        transform: `translateY(-50%) scale(${0.86 + 0.14 * pop})`, textAlign: 'center', color: 'white',
        fontFamily: BLACK, fontWeight: 900, fontSize: size, lineHeight: p.caption.lineHeight || 1.15,
        textShadow: OUTLINE, opacity: a, textWrap: 'balance', wordSpacing: '0.08em'} as React.CSSProperties}>
        {toks.map((w, i) => {
          const wd = page.words[i] !== undefined ? p.words[page.words[i]] : undefined;
          const now = wd ? wd.s <= t && t < Math.max(wd.e, wd.s + 0.12) : false;
          return (
            <React.Fragment key={i}>
              <span style={{color: hi.has(i) ? p.palette.amber : 'white', display: 'inline-block',
                transform: `translateY(${now ? -4 : 0}px)`}}>{w}</span>{i < toks.length - 1 ? ' ' : null}
            </React.Fragment>
          );
        })}
      </div>
    );
  }
  return (
    <div style={{position: 'absolute', left: p.caption.left, width: p.caption.width, top: p.caption.y * p.height,
      transform: 'translateY(-50%)', textAlign: 'center', color: 'white', fontFamily: SERIF, fontStyle: 'italic',
      fontSize: p.caption.size, lineHeight: p.caption.lineHeight || 1.25, textShadow: SHADOW, opacity: a}}>
      {page.text}
    </div>
  );
};

const Cinematic: React.FC<ShortProps> = (p) => {
  const frame = useCurrentFrame();
  const t = frame / p.fps;
  const fadeFrames = Math.round(p.fade * p.fps);
  const loopA = p.scenes.length > 1 ? interpolate(t, [p.duration - p.loopBlend, p.duration], [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 0;
  const cam = cueLook(p.fx || [], p.scenes, t);
  return (
    <AbsoluteFill style={{backgroundColor: p.palette.background}}>
      <AbsoluteFill style={{transform: `translate(${cam.x}px, ${cam.y}px) scale(${cam.scale})`,
        transformOrigin: '50% 45%'}}>
        {p.scenes.map((s, k) => {
          const from = Math.round(s.start * p.fps);
          const until = k + 1 < p.scenes.length ? Math.round(p.scenes[k + 1].start * p.fps) + fadeFrames
            : Math.ceil(p.duration * p.fps);
          return (
            <Sequence key={k} from={from} durationInFrames={Math.max(1, until - from)} layout="none">
              <CinemaScene scene={s} index={k} p={p} />
            </Sequence>
          );
        })}
      </AbsoluteFill>
      {cam.flash > 0 ? <AbsoluteFill style={{backgroundColor: 'white', opacity: cam.flash}} /> : null}
      {loopA > 0 ? (
        <AbsoluteFill style={{opacity: loopA}}>
          <Img src={staticFile(p.scenes[0].image)} />
        </AbsoluteFill>
      ) : null}
      <LineCaption p={p} />
      {p.audio ? <Audio src={staticFile(p.audio)} /> : null}
    </AbsoluteFill>
  );
};

export const Short: React.FC<ShortProps> = (p) => {
  if (p.look === 'hud') {
    return <Cinematic {...p} />;
  }
  const frame = useCurrentFrame();
  const t = frame / p.fps;
  const fadeFrames = Math.round(p.fade * p.fps);
  const loopA = p.scenes.length > 1 ? interpolate(t, [p.duration - p.loopBlend, p.duration], [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 0;
  return (
    <AbsoluteFill style={{backgroundColor: p.palette.background}}>
      {p.scenes.map((s, k) => {
        const from = Math.round(s.start * p.fps);
        const until = k + 1 < p.scenes.length ? Math.round(p.scenes[k + 1].start * p.fps) + fadeFrames
          : Math.ceil(p.duration * p.fps);
        return (
          <Sequence key={k} from={from} durationInFrames={Math.max(1, until - from)} layout="none">
            <SceneLayer scene={s} index={k} p={p} />
          </Sequence>
        );
      })}
      {loopA > 0 ? (
        <AbsoluteFill style={{opacity: loopA, transform: `scale(${1 + p.punch})`, transformOrigin: '50% 40%'}}>
          <Img src={staticFile(p.scenes[0].image)} />
        </AbsoluteFill>
      ) : null}
      <Captions p={p} />
      {p.audio ? <Audio src={staticFile(p.audio)} /> : null}
    </AbsoluteFill>
  );
};
