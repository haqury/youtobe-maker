export type SleepSettings = {
  enableChapterTitles: boolean;
  enableParticles: boolean;
  enableVignette: boolean;
  kenBurnsIntensity: number;
  crossfadeSeconds: number;
};

export type SleepStoryProps = {
  images: string[];
  voiceoverRel: string;
  ambientRel: string | null;
  chapters: string[];
  settings: SleepSettings;
};

export const defaultSleepProps: SleepStoryProps = {
  images: ['01.jpg'],
  voiceoverRel: 'current_episode/voiceover.mp3',
  ambientRel: null,
  chapters: [],
  settings: {
    enableChapterTitles: true,
    enableParticles: true,
    enableVignette: true,
    kenBurnsIntensity: 0.85,
    crossfadeSeconds: 1.5,
  },
};