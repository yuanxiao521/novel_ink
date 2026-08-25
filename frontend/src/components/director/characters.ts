/** 导演台 3 位角色：静态初始数据 + 运行时覆盖（SSE） */
import type { CharOverride } from '../../hooks/useDirectorSim';
import type { Belief } from '../../types/types';
import chinImg from '../../assets/portrait-chenmo.png';
import liwImg from '../../assets/portrait-liwen.png';
import zhouImg from '../../assets/portrait-zhoushen.png';

export interface CharSpec {
  id: string;
  name: string;
  portrait: string;
  mood: string;
  moodColor?: string;
  goal: string;
  weight: number;
  emotion: number;
  beliefs: Belief[];
}

export const CHARS: CharSpec[] = [
  {
    id: 'chenmo',
    name: '陈默',
    portrait: chinImg,
    mood: '警觉',
    goal: '查明真相',
    weight: 0.7,
    emotion: 75,
    beliefs: [
      { channel: 'perceived', text: '保险柜被动过' },
      { channel: 'inferred', text: '李文有事隐瞒' },
      { channel: 'told', text: '周婶十点送茶' },
    ],
  },
  {
    id: 'liwen',
    name: '李文',
    portrait: liwImg,
    mood: '紧张',
    moodColor: 'var(--accent-red)',
    goal: '自保',
    weight: 0.8,
    emotion: 65,
    beliefs: [
      { channel: 'perceived', text: '陈默发现文件' },
      { channel: 'inferred', text: '陈默起疑了' },
      { channel: 'told', text: '周婶在楼下' },
    ],
  },
  {
    id: 'zhoushen',
    name: '周婶',
    portrait: zhouImg,
    mood: '平静',
    moodColor: 'var(--accent-green)',
    goal: '守住家宅',
    weight: 0.4,
    emotion: 25,
    beliefs: [
      { channel: 'perceived', text: '两人在书房' },
      { channel: 'inferred', text: '气氛不对' },
    ],
  },
];

export function charOf(id: string): CharSpec {
  return CHARS.find((c) => c.id === id) || CHARS[0];
}

export function charName(id: string): string {
  return charOf(id).name;
}

export function effectiveOverride(
  spec: CharSpec,
  ov: CharOverride | undefined,
): { mood: string; moodColor?: string } {
  if (!ov?.mood) return { mood: spec.mood, moodColor: spec.moodColor };
  return {
    mood: ov.mood,
    moodColor: spec.id === 'chenmo' ? undefined : undefined,
  };
}