/**
 * 与后端 SSE / state 接口对应的事件与状态类型。
 * 字段名与 backend/schemas 保持一致，方便一比一对接。
 */

export type BeliefChannel = 'perceived' | 'told' | 'inferred';

export interface Belief {
  channel: BeliefChannel;
  text: string;
}

export interface EventPayload {
  kind?: string;
  text?: string;
  hints?: string[];
  new_fact?: string;
  actor?: string;
}

export interface StoryEvent {
  id: string;
  type: string;
  payload: EventPayload;
  guard_flags?: string[];
}

export interface GuardState {
  last_turn_blocks: number;
  fuse_counts: Record<string, number>;
}

export interface SimState {
  turn: number;
  tension: number;
  tension_trend?: string;
  characters?: string[];
  beliefs?: Record<string, Belief[]>;
  guard?: GuardState;
  paused?: boolean;
  empty_world?: boolean;
  world?: {
    facts?: Array<{ id: string; text: string }>;
    env_conds?: string[];
  };
}

export interface RaiseRequest {
  pending: boolean;
  reason?: string;
}

export const CHANNEL_TAG: Record<BeliefChannel, string> = {
  perceived: '亲见',
  told: '被告知',
  inferred: '推测',
};

export const CHANNEL_CLS: Record<BeliefChannel, string> = {
  perceived: 'tag-witness',
  told: 'tag-told',
  inferred: 'tag-infer',
};

// 用 IPv4 字面量直连后端，避免 localhost→IPv6 连不上（后端绑定 127.0.0.1）。
// 可用 window.API_BASE 覆盖（如指向其他后端地址）。
export const API_BASE =
  (window as unknown as { API_BASE?: string }).API_BASE ?? 'http://127.0.0.1:8000';