import {Composition} from 'remotion';
import {Short, ShortProps, defaults} from './Short';

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Short"
    component={Short}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={300}
    defaultProps={defaults}
    calculateMetadata={({props}: {props: ShortProps}) => ({
      durationInFrames: Math.max(1, Math.ceil(props.duration * props.fps)),
      fps: props.fps,
      width: props.width,
      height: props.height,
    })}
  />
);
