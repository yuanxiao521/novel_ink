import { useEffect, useReducer, useRef, useState } from 'react';
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
  /** 本回合正在感知（思考流式开始前的"灯亮"态） */
  perceiving?: boolean;
}

/** 回合归档（后端 TurnArchive 对应） */
export interface TurnArchive {
  turn: number;
  tension: number;
  tension_trend: string;
  cls: string;
  summary: string;
  prose: string;
  events: Array<{ id: string; actor: string; kind: string; text: string }>;
}

const TYPE_CLS: Record<string, string> = {
  conflict: 'type-conflict',
  dialogue: 'type-dialogue',
  action: 'type-action',
  info: 'type-info',
};
const PRIORITY: Record<string, number> = { conflict: 0, dialogue: 1, action: 2, info: 3 };

export interface DirectorCloseReport {
  scene_summary?: string;
  foreshadow_updates?: Array<{ id: string; status: string; reason?: string }>;
  character_arc_deltas?: Record<string, string>;
  causality?: Array<{ event_id: string; caused_by: string; causal_pressure: number }>;
  next_scene_hint?: string;
}

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
  archives: TurnArchive[]; // 回合归档（历史）
  viewing: TurnArchive | null; // 当前正在查看的历史回合（null=实时）
  closeReport: DirectorCloseReport | null; // 场景收束长程汇报（S3）
  emptyWorld: boolean; // 空黑板：无角色且无 facts（引导选角）
  charIds: string[]; // 当前上场角色 id 列表
  worldFacts: Array<{ id: string; text: string }>; // 世界事实（全局视角）
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
  archives: [],
  viewing: null,
  closeReport: null,
  emptyWorld: false,
  charIds: [],
  worldFacts: [],
};

type Action =
  | { type: 'STATE'; st: SimState }
  | { type: 'RUN'; text: string; ok: boolean }
  | { type: 'TURN_START'; turn: number }
  | { type: 'PERCEIVE'; charId: string }
  | { type: 'THINK'; charId: string; thought: string }
  | { type: 'THINK_DELTA'; charId: string; delta: string }
  | { type: 'ACT'; charId: string; events: StoryEvent[] }
  | { type: 'DIRECTOR'; d: { tension?: number; trend?: string; hint?: string; injected?: string[]; raise?: RaiseRequest } }
  | { type: 'GUARD'; guard: GuardState }
  | { type: 'PROSE'; text: string }
  | { type: 'PROSE_DELTA'; delta: string }
  | { type: 'TURN_END'; turn: number }
  | { type: 'DONE'; raisePending?: boolean; paused?: boolean }
  | { type: 'DIRECTOR_CLOSE'; report: DirectorCloseReport }
  | { type: 'RAISE_HIDE' }
  | { type: 'HISTORY'; archives: TurnArchive[] }
  | { type: 'VIEW'; archive: TurnArchive | null }
  | { type: 'RESET_AFTER_REWIND'; turn?: number };

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
      if (typeof a.st.turn === 'number') next.turn = a.st.turn;
      if (typeof a.st.tension === 'number') {
        next.tension = { val: a.st.tension, trend: a.st.tension_trend || 'flat' };
      }
      if (a.st.characters) next.charIds = a.st.characters as string[];
      if (a.st.beliefs) next.beliefs = { ...a.st.beliefs };
      if (a.st.guard) next.guard = { blocks: a.st.guard.last_turn_blocks || 0, fuse: fused(a.st.guard) };
      if (typeof a.st.empty_world === 'boolean') next.emptyWorld = a.st.empty_world;
      if (a.st.world?.facts) next.worldFacts = a.st.world.facts;
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
    case 'PERCEIVE': {
      // 本回合开始感知：清掉上一回合的思考/行动，显示"正在感知…"
      const o = { ...(s.overrides[a.charId] || {}) };
      o.thought = undefined;
      o.action = undefined;
      o.perceiving = true;
      return { ...s, overrides: { ...s.overrides, [a.charId]: o } };
    }
    case 'THINK': {
      const o = { ...(s.overrides[a.charId] || {}) };
      o.thought = a.thought || undefined;
      o.perceiving = false;
      return { ...s, overrides: { ...s.overrides, [a.charId]: o } };
    }
    case 'THINK_DELTA': {
      // 逐 token 思考：追加到该角色当前思考文本（前一个 delta 是同一角色的思考）
      const o = { ...(s.overrides[a.charId] || {}) };
      o.thought = (o.thought ?? '') + a.delta;
      o.perceiving = false;
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
        // 导演可见产物：注入列表（事件/曝光/调权）在前，舞台提示在后 → 作者能看到导演在"干活"
        const injected = a.d.injected?.length ? [...a.d.injected] : [];
        next.hints = [...injected, a.d.hint];
        if (a.d.raise?.pending && a.d.raise.reason) next.hints = [...injected, a.d.hint, `作者介入点：${a.d.raise.reason}`];
        // 回合节点摘要：用导演产物（短），不再由行动长文本截断
        const brief = a.d.hint.replace(/^舞台提示：/, '') || next.curAcc.summary;
        next.curAcc = { ...next.curAcc, summary: brief.slice(0, 14) || next.curAcc.summary };
      }
      if (a.d.raise) next.raise = a.d.raise;
      return next;
    }
    case 'GUARD':
      return { ...s, guard: { blocks: a.guard.last_turn_blocks || 0, fuse: fused(a.guard) } };
    case 'PROSE':
      return { ...s, proseInited: true, prose: [...s.prose, a.text] };
    case 'PROSE_DELTA': {
      // 逐 token 追加：追加到当前成文段的末尾（无内容则开新段）
      const prose = s.prose.length
        ? [...s.prose.slice(0, -1), s.prose[s.prose.length - 1] + a.delta]
        : [a.delta];
      return { ...s, proseInited: true, prose };
    }
    case 'TURN_END': {
      const lastFin = { ...s.lastFin, [a.turn]: s.curAcc };
      return finalizePending({ ...s, lastFin, curAcc: { cls: 'type-info', summary: '' } });
    }
    case 'DONE': {
      const next = finalizePending(s);
      // ended/converged → 已收束；raise_pending → 等待导演介入；paused → 已暂停
      if (a.raisePending) {
        next.runText = '等待导演介入';
        next.runOk = !a.raisePending;
      } else if (a.paused) {
        next.runText = '已暂停';
        next.runOk = true;
      } else {
        next.runText = '已收束';
        next.runOk = false;
      }
      return next;
    }
    case 'DIRECTOR_CLOSE': {
      // 场景收束长程汇报：更新 run 状态 + 展示摘要/伏笔推进
      const n = a.report?.foreshadow_updates?.length ?? 0;
      const next: SimUIState = {
        ...s,
        closeReport: a.report,
        runText: `收束汇报 · ${a.report?.scene_summary || '本场完结'}${n ? `（伏笔推进 ${n} 条）` : ''}`,
        runOk: true,
      };
      return next;
    }
    case 'RAISE_HIDE':
      return { ...s, raise: s.raise ? { ...s.raise, pending: false } : null };
    case 'HISTORY':
      return { ...s, archives: a.archives };
    case 'VIEW':
      return { ...s, viewing: a.archive };
    case 'RESET_AFTER_REWIND': {
      // 回退后重置：回到实时（viewing=null），清理后续回合展示
      // turn 从后端返回的状态中获取，确保与后端同步
      const rewindTurn = a.turn ?? (s.archives.length ? s.archives[s.archives.length - 1].turn : s.turn);
      return {
        ...s,
        viewing: null,
        finishedTurns: s.archives.map((arc) => ({
          turn: arc.turn, cls: arc.cls, summary: arc.summary,
        })),
        turn: rewindTurn,
        current: null,
        curAcc: { cls: 'type-info', summary: '' },
        eventCards: [],
        prose: [],
        envRows: [],
        hints: [],
        raise: null,
        overrides: {},
        proseInited: false,
        eventInited: false,
        envInited: false,
      };
    }
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

export function useDirectorSim(sceneId?: string, bookId?: string, chapterId?: string, startFresh?: boolean) {
  // 不再兜底 betrayal_night：无 sceneId 时不建 sim（由页面层引导"先选择场景"）
  const targetScene = sceneId || '';
  const [state, dispatch] = useReducer(reducer, initState);
  const simIdRef = useRef<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const [playing, setPlaying] = useState(false); // 播放状态（驱动 UI 重渲染）
  const playingRef = useRef(false); // 同步 ref，避免闭包过期
  const [simId, setSimId] = useState<string | null>(null); // sim 实例（导演对话等需要）

  const parse = (e: MessageEvent): Record<string, unknown> => {
    try {
      return JSON.parse(e.data as string) as Record<string, unknown>;
    } catch {
      return {};
    }
  };

  const closeStream = () => {
    if (esRef.current) {
      try { esRef.current.close(); } catch { /* noop */ }
      esRef.current = null;
    }
    playingRef.current = false;
    setPlaying(false);
  };

  // 拉取回合归档（时间线查看/回退），随时可用以刷新最新回合
  const refreshHistory = async () => {
    const sid = simIdRef.current;
    if (!sid) return;
    try {
      const hist = await fetch(`${API_BASE}/api/v1/sims/${sid}/history`).then((h) => h.json());
      if (Array.isArray(hist.archives)) dispatch({ type: 'HISTORY', archives: hist.archives });
    } catch {
      /* history 可选，失败不阻塞 */
    }
  };

  const connectStream = () => {
    const sid = simIdRef.current;
    if (!sid) return;
    closeStream();
    playingRef.current = true;
    setPlaying(true);
    const es = new EventSource(`${API_BASE}/api/v1/sims/${sid}/stream`);
    esRef.current = es;

    es.addEventListener('turn_start', (e) => {
      const d = parse(e) as { turn: number };
      dispatch({ type: 'TURN_START', turn: d.turn });
    });
    es.addEventListener('character_think', (e) => {
      const d = parse(e) as { char_id: string; thought?: unknown };
      let text = '';
      if (d.thought && typeof d.thought === 'object') {
        const obj = d.thought as { thought?: unknown };
        text = typeof obj.thought === 'string' ? obj.thought : '';
      } else if (typeof d.thought === 'string') {
        text = d.thought;
      }
      dispatch({ type: 'THINK', charId: d.char_id, thought: text });
    });
    es.addEventListener('character_think_delta', (e) => {
      const d = parse(e) as { char_id: string; delta?: string };
      if (d.char_id && typeof d.delta === 'string' && d.delta) {
        dispatch({ type: 'THINK_DELTA', charId: d.char_id, delta: d.delta });
      }
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
        injected?: string[];
        raise_request?: RaiseRequest;
      };
      dispatch({
        type: 'DIRECTOR',
        d: {
          tension: d.tension,
          trend: d.tension_trend,
          hint: d.hint,
          injected: Array.isArray(d.injected) ? d.injected : undefined,
          raise: d.raise_request,
        },
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
    es.addEventListener('prose_delta', (e) => {
      const d = parse(e) as { delta?: string };
      if (d.delta) dispatch({ type: 'PROSE_DELTA', delta: d.delta });
    });
    es.addEventListener('turn_end', (e) => {
      const d = parse(e) as { turn: number };
      dispatch({ type: 'TURN_END', turn: d.turn });
      // 每回合结束后刷新归档 → 时间线立即出现最新回合（可查看/回退，无需等 done）
      void refreshHistory();
    });
    es.addEventListener('done', (e) => {
      const d = parse(e) as {
        raise_pending?: boolean;
        paused?: boolean;
        next_scene?: { next_scene_id: string; next_scene_title: string } | null;
      };
      closeStream();
      dispatch({ type: 'DONE', raisePending: !!d.raise_pending, paused: !!d.paused });
      // 收束/暂停后刷新归档，保证时间线完整
      void refreshHistory();
      // —— S3 场景收束自动续场：有 next_scene → 自动开下一场并重连 ——
      if (d.next_scene?.next_scene_id) {
        const ns = d.next_scene;
        dispatch({ type: 'RUN', text: `本场收束，进入「${ns.next_scene_title}」…`, ok: true });
        void (async () => {
          try {
            const started = await fetch(`${API_BASE}/api/v1/sims`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ scene_id: ns.next_scene_id, resume: false }),
            }).then((r) => r.json()) as { sim_id: string };
            if (!started.sim_id) throw new Error('换场未返回 sim_id');
            simIdRef.current = started.sim_id;
            setSimId(started.sim_id);
            const st = await fetch(`${API_BASE}/api/v1/sims/${started.sim_id}/state`).then((r) => r.json()) as SimState;
            dispatch({ type: 'STATE', st });
            dispatch({ type: 'RUN', text: '已进入下一场，自动播放中…', ok: true });
            connectStream();
          } catch (e) {
            console.warn('[导演台] 自动换场失败：', e);
            dispatch({ type: 'RUN', text: '换场失败，点播放重试', ok: false });
          }
        })();
      }
    });
    es.addEventListener('director_close', (e) => {
      const report = parse(e) as DirectorCloseReport;
      dispatch({ type: 'DIRECTOR_CLOSE', report });
    });
    es.onerror = () => {
      closeStream();
      dispatch({ type: 'RUN', text: '已暂停', ok: true });
    };
  };

  // 挂载：按 sceneId 启动/恢复 sim + 拉初始状态，不自动连流；无 sceneId 不做任何请求
  useEffect(() => {
    if (!targetScene) return;
    let cancelled = false;
    const fetchJson = async (url: string, opts?: RequestInit) => {
      const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res.json();
    };
    (async () => {
      try {
        // 四层装配：sceneId 必带；book/chapter 从路由 state 取（可选）
        const started = await fetchJson(`${API_BASE}/api/v1/sims`, {
          method: 'POST',
          body: JSON.stringify({
            book_id: bookId ?? undefined,
            chapter_id: chapterId ?? undefined,
            scene_id: targetScene,
            resume: startFresh ? false : true,
          }),
        }) as { sim_id: string; resumed: boolean };
        if (!started.sim_id) throw new Error('启动 sim 未返回 sim_id');
        simIdRef.current = started.sim_id;
        setSimId(started.sim_id);
        const st = await fetchJson(`${API_BASE}/api/v1/sims/${started.sim_id}/state`) as SimState;
        if (!cancelled) dispatch({ type: 'STATE', st });
        // 拉回合归档（时间线回退查看）
        try {
          const hist = await fetchJson(`${API_BASE}/api/v1/sims/${started.sim_id}/history`) as { archives: TurnArchive[] };
          if (!cancelled && Array.isArray(hist.archives)) dispatch({ type: 'HISTORY', archives: hist.archives });
        } catch {
          /* history 可选，失败不阻塞 */
        }
        if (!cancelled) {
          // 恢复上次（resumed=true）→ 显示已推进内容 + 待播放；新 sim → 就绪
          dispatch({ type: 'RUN', text: started.resumed ? '已恢复（上次进度）' : '就绪', ok: true });
        }
      } catch (e) {
        console.warn('[导演台] 后端连接失败，回退为静态展示：', e);
        if (!cancelled) dispatch({ type: 'RUN', text: '未连接', ok: false });
      }
    })();
    return () => { cancelled = true; closeStream(); };
    // sceneId 固定（路由参数）；startFresh 变化时也要重开 sim（入口选"新开/继续"）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sceneId, startFresh]);

  // 播放控制：resume（清除暂停标记）+ 重连 SSE
  const resumeAndConnect = () => {
    if (playingRef.current) return;
    const sid = simIdRef.current;
    if (!sid) return;
    fetch(`${API_BASE}/api/v1/sims/${sid}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((st: SimState | null) => {
        if (st) dispatch({ type: 'STATE', st });
        dispatch({ type: 'RUN', text: '运行中', ok: true });
        connectStream();
      })
      .catch((e) => console.warn('[导演台] resume 失败：', e));
  };
  const play = () => {
    if (playingRef.current) return;
    const sid = simIdRef.current;
    if (!sid) return;
    if (state.raise?.pending) {
      dispatch({ type: 'RUN', text: '请先处理导演举手', ok: false });
      return;
    }
    resumeAndConnect();
  };
  // 事件暂停：告知后端在回合边界停住（不中断正在流的回合），随后断开 SSE
  const pause = () => {
    if (!playingRef.current) return;
    const sid = simIdRef.current;
    if (!sid) return;
    closeStream();
    dispatch({ type: 'RUN', text: '暂停中…', ok: true });
    fetch(`${API_BASE}/api/v1/sims/${sid}/pause`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((st: SimState | null) => {
        if (st) dispatch({ type: 'STATE', st });
        dispatch({ type: 'RUN', text: '已暂停', ok: true });
      })
      .catch((e) => console.warn('[导演台] pause 失败：', e));
  };
  const step = () => {
    const sid = simIdRef.current;
    if (!sid) return;
    if (state.raise?.pending) {
      dispatch({ type: 'RUN', text: '请先处理导演举手', ok: false });
      return;
    }
    fetch(`${API_BASE}/api/v1/sims/${sid}/step?n=1`, { method: 'POST', headers: { 'Content-Type': 'application/json' } })
      .then((r) => r.json())
      .then((st: SimState) => {
        dispatch({ type: 'STATE', st });
        dispatch({ type: 'RUN', text: '已步进', ok: true });
      })
      .catch((e) => console.warn('[导演台] step 失败：', e));
  };

  const resolveRaise = (action: 'accept' | 'reject') => {
    const sid = simIdRef.current;
    if (!sid) return;
    fetch(`${API_BASE}/api/v1/sims/${sid}/intervene`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((st: SimState | null) => {
        dispatch({ type: 'RAISE_HIDE' });
        if (st) dispatch({ type: 'STATE', st });
        if (action === 'accept') {
          // 同意 → 自动继续推演（resume + 重连 SSE），不再需要手动点播放
          resumeAndConnect();
        } else {
          dispatch({ type: 'RUN', text: '已驳回，等你点播放继续', ok: true });
        }
      })
      .catch((e) => console.warn('[导演台] intervene 失败：', e));
  };

  // 时间线：查看某回合（仅前端展示，不改状态）
  const viewTurn = (turn: number) => {
    const arc = state.archives.find((a) => a.turn === turn) ?? null;
    dispatch({ type: 'VIEW', archive: arc });
  };
  const exitView = () => dispatch({ type: 'VIEW', archive: null });

  // 时间线：回退到某回合（后端截断事件，可从此重演）
  const rewindTo = (turn: number) => {
    const sid = simIdRef.current;
    if (!sid) return;
    closeStream();
    fetch(`${API_BASE}/api/v1/sims/${sid}/rewind?turn=${turn}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        const st = (await r.json()) as SimState;
        dispatch({ type: 'STATE', st });
        // 先重置 UI（用后端返回的 turn），再刷新归档
        dispatch({ type: 'RESET_AFTER_REWIND', turn: st.turn });
        dispatch({ type: 'RUN', text: `已回退到 T-${turn}`, ok: true });
        // 刷新归档（回退后后端只保留到目标回合）
        const hist = await fetch(`${API_BASE}/api/v1/sims/${sid}/history`).then((h) => h.json());
        if (Array.isArray(hist.archives)) dispatch({ type: 'HISTORY', archives: hist.archives });
      })
      .catch((e) => console.warn('[导演台] rewind 失败：', e));
  };

  // 拉取最新 sim state（选角/介入等操作后刷新）
  const refresh = async () => {
    const sid = simIdRef.current;
    if (!sid) return;
    try {
      const st = (await fetch(`${API_BASE}/api/v1/sims/${sid}/state`).then((r) => r.json())) as SimState;
      dispatch({ type: 'STATE', st });
    } catch {
      /* state 拉取失败不阻塞 */
    }
  };

  return {
    state,
    playing,
    simId,
    play,
    pause,
    step,
    viewTurn,
    exitView,
    rewindTo,
    agreeRaise: () => resolveRaise('accept'),
    rejectRaise: () => resolveRaise('reject'),
    refresh,
  };
}