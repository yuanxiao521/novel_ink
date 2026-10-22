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
  return CHARS.find((c) => c.id === id) || { id, name: id, portrait: '', mood: '—', goal: '', weight: 0, emotion: 0, beliefs: [] };
}

/** 静态库里有没有这个角色（有 = 演示角色，带立绘/权重/情绪基线） */
export function isDemoChar(id: string): boolean {
  return CHARS.some((c) => c.id === id);
}

/**
 * v1.63：真实书的角色信息（来自 /books/{id}/characters）。
 *
 * 背景：本文件的 CHARS 只有 3 个**写死的演示角色**。真实书里 sim 的 charIds 是
 * char-202044d7 这类库 id，charOf() 查不到就回落成 {name: id, portrait: ''}
 * → 角色栏显示成原始 id + 破图 + 空目标（作者反馈「角色配置展示不全」）。
 * 现在：名字/档位/目标从接口来，立绘没有就用首字头像。
 */
export interface CharInfo {
  name: string;
  tier?: string;
  goal?: string;
}

/** 角色档位 → 中文（与 engine/cast_roles.py 的 5 档一致） */
export const TIER_LABEL: Record<string, string> = {
  protagonist: '主角',
  major: '关键配角',
  antagonist: '反派',
  minor: '次要',
  extra: '背景',
};

/** 角色显示名：真实书 → 演示库 → 原 id（绝不显示空白） */
export function charLabel(id: string, chars?: Record<string, CharInfo>): string {
  const real = chars?.[id]?.name?.trim();
  if (real) return real;
  const demo = CHARS.find((c) => c.id === id);
  return demo ? demo.name : id;
}

/** 没有立绘时的头像字（名字首字） */
export function charInitial(name: string): string {
  return (name || '?').trim().slice(0, 1) || '?';
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