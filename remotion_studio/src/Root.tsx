import React from 'react';
import {Composition} from 'remotion';
import {SleepStoryComposition, calculateSleepMetadata} from './SleepStoryComposition';
import {defaultSleepProps} from './types';

const FPS_PLACEHOLDER = 30 * 60;

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="SleepStoryComposition"
      component={SleepStoryComposition}
      durationInFrames={FPS_PLACEHOLDER}
      fps={30}
      width={1920}
      height={1080}
      defaultProps={defaultSleepProps}
      calculateMetadata={calculateSleepMetadata}
    />
  );
};