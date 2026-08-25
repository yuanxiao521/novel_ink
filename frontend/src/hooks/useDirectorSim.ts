import { useEffect, useReducer, useRef } from 'react';
import {
  API_BASE,
  type Belief,
  type GuardState,
  type RaiseRequest,
  type SimState,
  type StoryEvent,
} from '../types/types';

/** 回合节点（时间线） */
export interface TurnNode {
  turn: number;
  cls: string; // type-conflict / type-dialogue / type-action / type-info
  summary: string;
}

/** 黑板当前回合事件卡 */
export interface EventCard {
  id: string;
  cls: string;
  label: string;
  quote: string;
  source: string;
}

/** 角色运行时覆盖（SSE 注入） */
export interface CharOverride {
  mood?: string;
  thought?: string;
  action?: string;
}

const TYPE_CLS: Record<string, string> = {
  conflict: 'type-conflict',
  dialogue: 'type-dialogue',
  action: 'type-action',
  info: 'type-info',
};
const PRIORITY: Record<string, number> = { conflict: 0, dialogue: 1, action: 2, info: 3 };

export interface SimUIState {
  runText: string;
  runOk: boolean;
  turn: number | null;
  finishedTurns: TurnNode[]; // 已定稿回合
  current: TurnNode | null; // 进行中的回合节点
  curAcc: { cls: string; summary: string }; // 当前回合正在累积的类型/摘要
  lastFin: Record<number, { cls: string; summary: string }>;
  tension: { val: number; trend: string } | null;
  hints: string[];
  raise: RaiseRequest | null;
  guard: { blocks: number; fuse: number } | null;
  prose: string[];
  envRows: string[];
  eventCards: EventCard[];
  beliefs: Record<string, Belief[]>;
  overrides: Record<string, CharOverride>;
  proseInited: boolean;
  eventInited: boolean;
  envInited: boolean;
}

const initState: SimUIState = {
  runText: '连接中…',
  runOk: true,
  turn: null,
  finishedTurns: [],
  current: null,
  curAcc: { cls: 'type-info', summary: '' },
  lastFin: {},
  tension: null,
  hints: [],
  raise: null,
  guard: null,
  prose: [],
  envRows: [],
  eventCards: [],
  beliefs: {},
  overrides: {},
  proseInited: false,
  eventInited: false,
  envInited: false,
};

type Action =
  | { type: 'STATE'; st: SimState }
  | { type: 'RUN'; text: string; ok: boolean }
  | { type: 'TURN_START'; turn: number }
  | { type: 'THINK'; charId: string; thought: string }
  | { type: 'ACT'; charId: string; events: StoryEvent[] }
  | { type: 'DIRECTOR'; d: { tension?: number; trend?: string; hint?: string; raise?: RaiseRequest } }
  | { type: 'GUARD'; guard: GuardState }
  | { type: 'PROSE'; text: string }
  | { type: 'TURN_END'; turn: number }
  | { type: 'DONE'; raisePending: boolean }
  | { type: 'RAISE_HIDE' };

/* ---------- 工具（与 app.js 对齐） ---------- */

function evKind(ev: StoryEvent): string {
  const k = ev.payload?.kind;
  if (k) return k;
  if (ev.type === 'environment') return 'environment';
  if (ev.type === 'action') return 'action';
  return 'info';
}

function evText(ev: StoryEvent): string {
  const p = ev.payload;
  return p?.text || (Array.isArray(p?.hints) ? p.hints.join('；') : '');
}

const MOOD_WORDS: Array<[string, string[]]> = [
  ['警觉', ['警觉', '防备', '警惕', '盯']],
  ['怀疑', ['起疑', '怀疑', '多疑', '追问']],
  ['心虚', ['心虚', '强撑', '试探', '绞着', '闪躲']],
  ['愤怒', ['一拍桌', '猛拍', '怒', '质问', '发抖', '怒喝']],
  ['不安', ['不安', '焦躁', '忐忑', '咬唇']],
  ['愧疚', ['愧疚', '愧', '低语', '示弱', '让步']],
  ['冷静', ['冷静', '克制', '淡然', '平稳']],
  ['沉默', ['沉默', '不发', '闭嘴', '无言', '缓缓', '低头', '沉默地']],
  ['紧张', ['紧张', '绞着衣角', '手心', '慌乱']],
];
function detectMood(text: string): string | null {
  for (const [mood, words] of MOOD_WORDS) {
    if (words.some((w) => text.includes(w))) return mood;
  }
  return null;
}

const SPEAKER = { chenmo: '陈默', liwen: '李文', zhoushen: '周婶' } as Record<string, string>;
function speakerName(charId: string, ev: StoryEvent): string {
  const actor = ev.payload?.actor || '';
  return (actor ? SPEAKER[actor] : undefined) || SPEAKER[charId] || actor || charId;
}

function pad(n: number): string {
  return n < 10 ? '0' + n : String(n);
}

function finalizePending(s: SimUIState): SimUIState {
  const n = s.current?.turn;
  if (n == null) return s;
  const t = s.lastFin[n] || s.curAcc;
  const node: TurnNode = { turn: n, cls: t.cls || 'type-info', summary: t.summary || `回合 T-${pad(n)}` };
  return {
    ...s,
    finishedTurns: [...s.finishedTurns, node],
    current: null,
    lastFin: { ...s.lastFin },
  };
}

function reducer(s: SimUIState, a: Action): SimUIState {
  switch (a.type) {
    case 'STATE': {
      const next: SimUIState = { ...s };
      if (typeof a.st.tension === 'number') {
        next.tension = { val: a.st.tension, trend: a.st.tension_trend || 'flat' };
      }
      if (a.st.beliefs) next.beliefs = { ...a.st.beliefs };
      if (a.st.guard) next.guard = { blocks: a.st.guard.last_turn_blocks || 0, fuse: fused(a.st.guard) };
      return next;
    }
    case 'RUN':
      return { ...s, runText: a.text, runOk: a.ok };
    case 'TURN_START': {
      let x = finalizePending(s);
      const curAcc = { cls: 'type-info', summary: '' };
      return {
        ...x,
        turn: a.turn,
        curAcc,
        current: { turn: a.turn, cls: 'type-info', summary: '推演中…' },
      };
    }
    case 'THINK': {
      const o = { ...(s.overrides[a.charId] || {}) };
      o.thought = a.thought || undefined;
      return { ...s, overrides: { ...s.overrides, [a.charId]: o } };
    }
    case 'ACT': {
      let next: SimUIState = { ...s };
      const last = a.events[a.events.length - 1];
      let lastKind = '';
      let lastTxt = '';
      for (const ev of a.events) {
        const kind = evKind(ev);
        const txt = evText(ev);
        lastKind = kind;
        lastTxt = txt;
        next = renderEventCard(next, a.charId, ev, kind, txt);
        next = renderEnvIfNeeded(next, ev, kind, txt);
        next = applyMood(next, a.charId, txt);
        next = accumulateTurnType(next, kind, txt);
      }
      if (last) {
        const o = { ...(next.overrides[a.charId] || {}) };
        o.action =
          lastKind === 'dialogue'
            ? `『${speakerName(a.charId, last)}』　${lastTxt}`
            : lastTxt || undefined;
        next = { ...next, overrides: { ...next.overrides, [a.charId]: o } };
      }
      return next;
    }
    case 'DIRECTOR': {
      const next: SimUIState = { ...s };
      if (typeof a.d.tension === 'number') {
        next.tension = { val: a.d.tension, trend: a.d.trend || 'flat' };
      }
      if (a.d.hint != null) {
        next.hints = [a.d.hint];
        if (a.d.raise?.pending && a.d.raise.reason) next.hints = [a.d.hint, `作者介入点：${a.d.raise.reason}`];
      }
      if (a.d.raise) next.raise = a.d.raise;
      return next;
    }
    case 'GUARD':
      return { ...s, guard: { blocks: a.guard.last_turn_blocks || 0, fuse: fused(a.guard) } };
    case 'PROSE':
      return { ...s, proseInited: true, prose: [...s.prose, a.text] };
    case 'TURN_END': {
      const lastFin = { ...s.lastFin, [a.turn]: s.curAcc };
      return finalizePending({ ...s, lastFin, curAcc: { cls: 'type-info', summary: '' } });
    }
    case 'DONE': {
      const next = finalizePending(s);
      next.runText = a.raisePending ? '等待导演介入' : '已收束';
      next.runOk = !a.raisePending;
      return next;
    }
    case 'RAISE_HIDE':
      return { ...s, raise: s.raise ? { ...s.raise, pending: false } : null };
    default:
      return s;
  }
}

function fused(g: GuardState): number {
  return Object.values(g.fuse_counts || {}).reduce((sum, v) => sum + v, 0);
}

function renderEventCard(s: SimUIState, charId: string, ev: StoryEvent, kind: string, txt: string): SimUIState {
  if (!txt) return s;
  const map: Record<string, { label: string; cls: string }> = {
    dialogue: { label: '对话', cls: 'type-dialogue' },
    action: { label: '行动', cls: 'type-action' },
    conflict: { label: '冲突', cls: 'type-conflict' },
    environment: { label: '环境', cls: 'type-info' },
  };
  const m = map[kind] || { label: '信息', cls: 'type-info' };
  const who = speakerName(charId, ev);
  const source =
    (kind === 'dialogue' ? `${who} 说` : `${who} · 展开行动`) +
    (ev.guard_flags?.length ? `　护栏: ${ev.guard_flags.join('、')}` : '');
  const card: EventCard = {
    id: ev.id || '',
    cls: m.cls,
    label: m.label,
    quote: kind === 'dialogue' ? `「${txt}」` : txt,
    source,
  };
  return { ...s, eventInited: true, eventCards: [...s.eventCards, card] };
}

function renderEnvIfNeeded(s: SimUIState, ev: StoryEvent, kind: string, txt: string): SimUIState {
  const p = ev.payload;
  const isEnv = ev.type === 'environment' || kind === 'environment' || !!(p?.new_fact && String(p.new_fact).length);
  if (!isEnv) return s;
  let t = kind === 'environment' || ev.type === 'environment' ? txt : '';
  if (!t && p?.new_fact && String(p.new_fact).length) t = String(p.new_fact);
  if (!t) return s;
  return { ...s, envInited: true, envRows: [...s.envRows, t] };
}

function applyMood(s: SimUIState, charId: string, text: string): SimUIState {
  if (!text) return s;
  const mood = detectMood(text);
  if (!mood) return s;
  const o = { ...(s.overrides[charId] || {}) };
  o.mood = mood;
  return { ...s, overrides: { ...s.overrides, [charId]: o } };
}

function accumulateTurnType(s: SimUIState, kind: string, txt: string): SimUIState {
  const map = TYPE_CLS[kind];
  const key = map || 'type-info';
  const curKey = s.curAcc.cls.replace('type-', '');
  const curPrio = PRIORITY[curKey] !== undefined ? PRIORITY[curKey] : 4;
  const prio = PRIORITY[kind];
  let summary = s.curAcc.summary;
  if (txt) summary = txt.replace(/^（.*?）/, '').slice(0, 12) || summary;
  const cls = prio !== undefined && prio < curPrio ? key : s.curAcc.cls;
  return { ...s, curAcc: { cls, summary } };
}

/* ---------- Hook ---------- */

export function useDirectorSim() {
  const [state, dispatch] = useReducer(reducer, initState);
  const simIdRef = useRef<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const parse = (e: MessageEvent): Record<string, unknown> => {
    try {
      return JSON.parse(e.data as string) as Record<string, unknown>;
    } catch {
      return {};
    }
  };

  const connectStream = () => {
    const sid = simIdRef.current;
    if (!sid) return;
    if (esRef.current) {
      try {
        esRef.current.close();
      } catch {
        /* noop */
      }
    }
    const es = new EventSource(`${API_BASE}/api/v1/sims/${sid}/stream`);
    esRef.current = es;

    es.addEventListener('turn_start', (e) => {
      const d = parse(e) as { turn: number };
      dispatch({ type: 'TURN_START', turn: d.turn });
    });
    es.addEventListener('character_think', (e) => {
      const d = parse(e) as { char_id: string; thought?: string };
      dispatch({ type: 'THINK', charId: d.char_id, thought: d.thought || '' });
    });
    es.addEventListener('character_act', (e) => {
      const d = parse(e) as { char_id: string; events: StoryEvent[] };
      dispatch({ type: 'ACT', charId: d.char_id, events: d.events || [] });
    });
    es.addEventListener('director', (e) => {
      const d = parse(e) as {
        tension?: number;
        tension_trend?: string;
        hint?: string;
        raise_request?: RaiseRequest;
      };
      dispatch({
        type: 'DIRECTOR',
        d: { tension: d.tension, trend: d.tension_trend, hint: d.hint, raise: d.raise_request },
      });
    });
    es.addEventListener('guard', (e) => {
      const d = parse(e) as { guard: GuardState };
      if (d.guard) dispatch({ type: 'GUARD', guard: d.guard });
    });
    es.addEventListener('prose', (e) => {
      const d = parse(e) as { text?: string };
      if (d.text) dispatch({ type: 'PROSE', text: d.text });
    });
    es.addEventListener('turn_end', (e) => {
      const d = parse(e) as { turn: number };
      dispatch({ type: 'TURN_END', turn: d.turn });
    });
    es.addEventListener('done', (e) => {
      const d = parse(e) as { raise_pending?: boolean };
      if (esRef.current) {
        try {
          esRef.current.close();
        } catch {
          /* noop */
        }
        esRef.current = null;
      }
      dispatch({ type: 'DONE', raisePending: !!d.raise_pending });
    });
  };

  useEffect(() => {
    let cancelled = false;

    const initDirector = async () => {
      const fetchJson = async (url: string, opts?: RequestInit) => {
        const res = await fetch(url, {
          headers: { 'Content-Type': 'application/json' },
          ...opts,
        });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
      };
      try {
        const started = (await fetchJson(
          `${API_BASE}/api/v1/sims?scenario=betrayal_night`,
          { method: 'POST' },
        )) as { sim_id: string };
        if (!started.sim_id) throw new Error('建 sim 未返回 sim_id');
        simIdRef.current = started.sim_id;
        const st = (await fetchJson(`${API_BASE}/api/v1/sims/${started.sim_id}/state`)) as SimState;
        if (!cancelled) dispatch({ type: 'STATE', st });
        if (!cancelled) {
          connectStream();
          dispatch({ type: 'RUN', text: '运行中', ok: true });
        }
      } catch (e) {
        console.warn('[导演台] 后端连接失败，回退为静态展示：', e);
        if (!cancelled) dispatch({ type: 'RUN', text: '未连接', ok: false });
      }
    };

    initDirector();
    return () => {
      cancelled = true;
      if (esRef.current) {
        try {
          esRef.current.close();
        } catch {
          /* noop */
        }
        esRef.current = null;
      }
    };
    // connectStream 稳定；仅挂载时执行一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const resolveRaise = (action: 'accept' | 'reject') => {
    const sid = simIdRef.current;
    if (!sid) return;
    fetch(`${API_BASE}/api/v1/sims/${sid}/intervene`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    })
      .then(() => {
        dispatch({ type: 'RAISE_HIDE' });
        dispatch({ type: 'RUN', text: '运行中', ok: true });
        connectStream();
      })
      .catch((e) => console.warn('[导演台] intervene 失败：', e));
  };

  return { state, agreeRaise: () => resolveRaise('accept'), rejectRaise: () => resolveRaise('reject') };
}