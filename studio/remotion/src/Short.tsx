// Jarvis studio compositor: the scenes (Manim clips or still pictures) with a slow push-in, short
// crossfades, a punch-in on the hook, animated word-by-word captions and a seamless loop back into the
// first frame. Everything is driven by the props Jarvis writes (remotion_props.json); nothing here is
// downloaded or random, so the same props always give the same video.
import React from 'react';
import {
  AbsoluteFill, Audio, Img, OffthreadVideo, Sequence, interpolate, spring, staticFile,
  useCurrentFrame, useVideoConfig,
} from 'remotion';

type Scene = {start: number; end: number; image: string; video?: string; videoSeconds?: number; hold?: string};
type Word = {w: string; s: number; e: number};
type Page = {s: number; e: number; words: number[]};

export type ShortProps = {
  fps: number; width: number; height: number; duration: number; audio: string;
  scenes: Scene[]; words: Word[]; pages: Page[]; keywords: number[];
  palette: {cyan: string; amber: string; text: string; background: string};
  caption: {y: number; left: number; width: number; size: number; maxLines: number};
  fade: number; punch: number; loopBlend: number;
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

export const Short: React.FC<ShortProps> = (p) => {
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
