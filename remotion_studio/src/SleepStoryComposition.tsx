import React, {useMemo} from 'react';
import {
  AbsoluteFill,
  Audio,
  Img,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import type {CalculateMetadataFunction} from 'remotion';
import {getAudioDurationInSeconds} from '@remotion/media-utils';
import {defaultSleepProps, type SleepStoryProps} from './types';

const FPS = 30;
const AMBIENT_VOLUME = 0.08;

const kenBurns = (
  frame: number,
  durationFrames: number,
  index: number,
  intensity: number,
): {scale: number; x: number; y: number} => {
  const p = durationFrames <= 1 ? 1 : Math.min(1, Math.max(0, frame / durationFrames));
  const k = Math.max(0.2, Math.min(1.2, intensity));
  const pattern = index % 5;
  if (pattern === 0) {
    return {scale: 1 + 0.08 * k * p, x: 0, y: 0};
  }
  if (pattern === 1) {
    return {scale: 1.08 * k - 0.08 * k * p + (1 - k) * 0.04, x: 0, y: 0};
  }
  if (pattern === 2) {
    const drift = (p - 0.5) * 6 * k;
    return {scale: 1.09 * (0.92 + 0.08 * k), x: drift, y: drift * 0.6};
  }
  if (pattern === 3) {
    return {scale: 1.08 * (0.94 + 0.06 * k), x: interpolate(p, [0, 1], [-4 * k, 4 * k]), y: 0};
  }
  return {scale: 1.08 * (0.94 + 0.06 * k), x: interpolate(p, [0, 1], [4 * k, -4 * k]), y: 0};
};

const ImageSlide: React.FC<{
  src: string;
  durationFrames: number;
  index: number;
  kenBurnsIntensity: number;
  fadeFrames: number;
}> = ({src, durationFrames, index, kenBurnsIntensity, fadeFrames}) => {
  const frame = useCurrentFrame();
  const fadeIn = interpolate(frame, [0, fadeFrames], [0, 1], {extrapolateRight: 'clamp'});
  const fadeOut = interpolate(
    frame,
    [durationFrames - fadeFrames, durationFrames],
    [1, 0],
    {extrapolateLeft: 'clamp'},
  );
  const opacity = Math.min(fadeIn, fadeOut);
  const motion = kenBurns(frame, durationFrames, index, kenBurnsIntensity);

  return (
    <AbsoluteFill style={{opacity, backgroundColor: '#0a0a0c'}}>
      <Img
        src={staticFile(src)}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          transform: `scale(${motion.scale}) translate(${motion.x}%, ${motion.y}%)`,
        }}
      />
    </AbsoluteFill>
  );
};

const Particles: React.FC = () => {
  const frame = useCurrentFrame();
  const dots = useMemo(
    () =>
      Array.from({length: 48}, (_, i) => ({
        id: i,
        left: (i * 37) % 100,
        top: (i * 53) % 100,
        size: 2 + (i % 3),
        speed: 0.15 + (i % 5) * 0.04,
      })),
    [],
  );
  return (
    <AbsoluteFill style={{pointerEvents: 'none', mixBlendMode: 'screen'}}>
      {dots.map((d) => {
        const y = (d.top + frame * d.speed * 0.08) % 100;
        const opacity = 0.15 + (d.id % 4) * 0.08;
        return (
          <div
            key={d.id}
            style={{
              position: 'absolute',
              left: `${d.left}%`,
              top: `${y}%`,
              width: d.size,
              height: d.size,
              borderRadius: '50%',
              background: 'rgba(255,240,220,0.9)',
              opacity,
              filter: 'blur(0.5px)',
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};

const Vignette: React.FC = () => {
  const frame = useCurrentFrame();
  const breath = 0.55 + 0.08 * Math.sin((frame / FPS) * ((2 * Math.PI) / 6));
  return (
    <AbsoluteFill
      style={{
        pointerEvents: 'none',
        background: `radial-gradient(ellipse at center, transparent 42%, rgba(0,0,0,${breath}) 100%)`,
      }}
    />
  );
};

const ChapterTitle: React.FC<{title: string; durationFrames: number}> = ({title, durationFrames}) => {
  const frame = useCurrentFrame();
  const showFrames = Math.min(durationFrames, FPS * 4);
  const opacity = interpolate(frame, [0, 20, showFrames - 15, showFrames], [0, 0.92, 0.92, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <AbsoluteFill
      style={{
        justifyContent: 'flex-end',
        alignItems: 'flex-start',
        padding: '8% 10%',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          opacity,
          fontSize: 42,
          fontWeight: 500,
          color: 'rgba(255,245,230,0.92)',
          textShadow: '0 2px 24px rgba(0,0,0,0.65)',
          background: 'rgba(20,16,12,0.35)',
          padding: '16px 28px',
          borderRadius: 8,
          letterSpacing: '0.02em',
        }}
      >
        {title}
      </div>
    </AbsoluteFill>
  );
};

export const SleepStoryComposition: React.FC<SleepStoryProps> = (props) => {
  const cfg = props.settings ?? defaultSleepProps.settings;
  const {durationInFrames} = useVideoConfig();
  const count = Math.max(1, props.images.length);
  const crossfade = Math.round(cfg.crossfadeSeconds * FPS);
  const slideFrames = Math.floor((durationInFrames + crossfade * (count - 1)) / count);

  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      {props.images.map((img, index) => (
        <Sequence key={`${img}-${index}`} from={index * (slideFrames - crossfade)} durationInFrames={slideFrames}>
          <ImageSlide
            src={img}
            durationFrames={slideFrames}
            index={index}
            kenBurnsIntensity={cfg.kenBurnsIntensity}
            fadeFrames={crossfade}
          />
        </Sequence>
      ))}
      {cfg.enableChapterTitles &&
        props.chapters.map((title, index) => {
          const from = Math.round((index / Math.max(1, props.chapters.length)) * durationInFrames);
          return (
            <Sequence key={`ch-${index}`} from={from} durationInFrames={FPS * 5}>
              <ChapterTitle title={title} durationFrames={FPS * 5} />
            </Sequence>
          );
        })}
      {cfg.enableParticles && <Particles />}
      {cfg.enableVignette && <Vignette />}
      <Audio src={staticFile(props.voiceoverRel)} />
      {props.ambientRel ? <Audio src={staticFile(props.ambientRel)} volume={AMBIENT_VOLUME} /> : null}
    </AbsoluteFill>
  );
};

export const calculateSleepMetadata: CalculateMetadataFunction<SleepStoryProps> = async ({props}) => {
  const durationSec = await getAudioDurationInSeconds(staticFile(props.voiceoverRel));
  return {
    durationInFrames: Math.max(FPS * 5, Math.ceil(durationSec * FPS)),
    fps: FPS,
    width: 1920,
    height: 1080,
    props,
  };
};