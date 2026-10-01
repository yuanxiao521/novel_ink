/**
 * 四层目录 API 封装（书 → 章 → 场景 → 角色 / sim）。
 * 复用 types.ts 的 API_BASE；聚合树端点一次拉全防客户端 N+1。
 */
import { API_BASE } from '../types/types';

export interface BookMeta {
  id: string;
  title: string;
  genre: string;
  status: string;
  cover_init: string;
  chapter_count: number;
  synopsis?: string;
}

export interface ChapterMeta {
  id: string;
  book_id: string;
  title: string;
  summary: string;
  order_no: number;
  tone?: string;
  tension_curve?: string;
  word_target?: number;
}

export interface SceneMeta {
  id: string;
  chapter_id: string;
  title: string;
  scenario_def: string;
  cursor_pos: number;
  stage_desc?: string;
  goal?: string;
  content_desc?: string;
  scene_summary?: string;
  characters?: CharacterMeta[];
}

export interface CharacterMeta {
  id: string;
  name: string;
  spec: Record<string, unknown>;
}

export interface BookTree {
  id: string;
  title: string;
  genre: string;
  status: string;
  cover_init: string;
  chapter_count: number;
  synopsis?: string;
  worldview_json?: string;
  world_rules_json?: string;
  chapters: Array<ChapterMeta & { scenes: SceneMeta[] }>;
}

export interface SimStartResult {
  sim_id: string;
  resumed: boolean;
  scene_id: string;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export async function listBooks(): Promise<BookMeta[]> {
  return getJson<BookMeta[]>(`${API_BASE}/api/v1/books`);
}

export async function fetchBookTree(bookId: string): Promise<BookTree> {
  return getJson<BookTree>(`${API_BASE}/api/v1/books/${bookId}/tree`);
}

export async function listChapters(bookId: string): Promise<ChapterMeta[]> {
  return getJson<ChapterMeta[]>(`${API_BASE}/api/v1/books/${bookId}/chapters`);
}

export async function listScenes(chapterId: string): Promise<SceneMeta[]> {
  return getJson<SceneMeta[]>(`${API_BASE}/api/v1/chapters/${chapterId}/scenes`);
}

export async function createOrResumeSim(p: {
  book_id?: string;
  chapter_id?: string;
  scene_id: string;
  resume?: boolean;
}): Promise<SimStartResult> {
  const res = await fetch(`${API_BASE}/api/v1/sims`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...p, resume: p.resume ?? true }),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as SimStartResult;
}

/* ---------- 写接口（S1 规划页用） ---------- */

async function send<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function createBook(data: Partial<BookMeta>): Promise<{ id: string }> {
  return send('POST', '/api/v1/books', data);
}
export async function updateBook(id: string, data: Partial<BookMeta>): Promise<{ id: string }> {
  return send('PUT', `/api/v1/books/${id}`, data);
}
export async function deleteBook(id: string): Promise<void> {
  return send('DELETE', `/api/v1/books/${id}`);
}

export async function createChapter(bookId: string, data: Partial<ChapterMeta>): Promise<{ id: string }> {
  return send('POST', `/api/v1/books/${bookId}/chapters`, data);
}
export async function updateChapter(id: string, data: Partial<ChapterMeta>): Promise<{ id: string }> {
  return send('PUT', `/api/v1/chapters/${id}`, data);
}
export async function deleteChapter(id: string): Promise<void> {
  return send('DELETE', `/api/v1/chapters/${id}`);
}

export async function createScene(chapterId: string, data: Partial<SceneMeta>): Promise<{ id: string }> {
  return send('POST', `/api/v1/chapters/${chapterId}/scenes`, data);
}
export async function updateScene(id: string, data: Partial<SceneMeta>): Promise<{ id: string }> {
  return send('PUT', `/api/v1/scenes/${id}`, data);
}
export async function deleteScene(id: string): Promise<void> {
  return send('DELETE', `/api/v1/scenes/${id}`);
}

/** 场景级全量替换（主笔升级 · 部分修改）：传该章完整场景数组，服务端 diff 增删改。 */
export interface ScenePatchItem {
  id?: string;
  title: string;
  stage_desc?: string;
  goal?: string;
  content_desc?: string;
}

export async function replaceChapterScenes(
  chapterId: string,
  scenes: ScenePatchItem[],
): Promise<{ chapter_id: string; scenes: number }> {
  return send('PUT', `/api/v1/chapters/${chapterId}/scenes`, { scenes });
}

/* ---------- 正文落库（P0 · v1.2） ---------- */

export interface FinalizeResult {
  scene_id: string;
  chapter_id: string;
  book_id: string;
  title: string;
  word_count: number;
  prose: string;
}

export interface SceneProse {
  scene_id: string;
  chapter_id: string;
  title: string;
  prose: string;
  word_count: number;
  finalized: boolean;
}

export interface ChapterProse {
  chapter_id: string;
  book_id: string;
  title: string;
  order_no: number;
  word_count: number;
  scenes: Array<{ scene_id: string; title: string; prose: string; finalized: boolean }>;
  prose: string;
}

/** 作者手动定稿：聚合该 sim 回合成文 → 写入场景 final_prose（幂等覆盖）。 */
export async function finalizeSim(simId: string): Promise<FinalizeResult> {
  return send('POST', `/api/v1/sims/${simId}/finalize`);
}

export async function fetchSceneProse(sceneId: string): Promise<SceneProse> {
  return getJson<SceneProse>(`${API_BASE}/api/v1/scenes/${sceneId}/prose`);
}

export async function fetchChapterProse(chapterId: string): Promise<ChapterProse> {
  return getJson<ChapterProse>(`${API_BASE}/api/v1/chapters/${chapterId}/prose`);
}

/** 全书导出 markdown 下载地址（浏览器 <a href> 直开下载）。 */
export function exportBookUrl(bookId: string): string {
  return `${API_BASE}/api/v1/books/${bookId}/export`;
}

/* ---------- 导演介入三件套（v1.3） ---------- */

export interface InjectPalette {
  turn: number;
  /** 可曝光事实（当前运行时黑板 facts） */
  facts: Array<{ id: string; text: string; kind: string; visible_to: string[] }>;
  /** 角色与可调权动态目标 */
  characters: Array<{
    id: string;
    name: string;
    dynamic_goals: Array<{ id: string; text: string; weight: number }>;
  }>;
}

export interface InterveneResult {
  sim_id: string;
  action: string;
  ok: boolean;
  turn: number;
}

/** 介入工具下拉数据源：facts + 角色动态目标。 */
export async function fetchInjectPalette(simId: string): Promise<InjectPalette> {
  return getJson<InjectPalette>(`${API_BASE}/api/v1/sims/${simId}/inject-palette`);
}

/** 手动介入：inject_event / expose / adjust_weight（adjust_weight 的 reason 必填，后端 4xx 兜底）。 */
export async function interveneSim(
  simId: string,
  action: 'inject_event' | 'expose' | 'adjust_weight',
  payload: Record<string, unknown>,
): Promise<InterveneResult> {
  return send('POST', `/api/v1/sims/${simId}/intervene`, { action, payload });
}

/* ---------- 角色卡 CRUD（v1.3） ---------- */

export interface CharacterGoalSpec {
  id: string;
  text: string;
  weight: number;
  last_adjust_reason?: string;
}

/** ④层角色卡可编辑字段（docs/prompt核心设定.md）：spec_json 的 JSON 形态。 */
export interface CharacterSpec {
  id?: string;
  name: string;
  summary: string;
  traits: string[];
  voice: string;
  core_beliefs: string[];
  dynamic_goals: CharacterGoalSpec[];
  bottom_lines: string[];
  system_prompt: string;
  think_schema: string;
  decide_schema: string;
  static_world: string;
}

export interface CharacterCardMeta {
  id: string;
  book_id: string;
  scene_id?: string | null;
  name: string;
  spec_json: string;
}

export interface SceneDetail {
  id: string;
  chapter_id: string;
  title: string;
  scenario_def: string;
  cursor_pos: number;
  stage_desc?: string;
  goal?: string;
  content_desc?: string;
  scene_summary?: string;
  final_prose?: string;
  characters: CharacterCardMeta[];
}

/** 场景详情（含角色卡列表，GET /scenes/{id} 已内联 characters）。 */
export async function fetchSceneDetail(sceneId: string): Promise<SceneDetail> {
  return getJson<SceneDetail>(`${API_BASE}/api/v1/scenes/${sceneId}`);
}

/** 书级角色库（GET /books/{id}/characters）。 */
export async function listBookCharacters(bookId: string): Promise<CharacterCardMeta[]> {
  return getJson<CharacterCardMeta[]>(`${API_BASE}/api/v1/books/${bookId}/characters`);
}

/** 书级新建角色卡（POST /books/{id}/characters）。 */
export async function createBookCharacter(bookId: string, data: { name: string; spec_json?: string }): Promise<{ id: string }> {
  return send('POST', `/api/v1/books/${bookId}/characters`, data);
}

/** 书级新建（兼容旧调用，走书级端点）。 */
export async function createCharacter(bookId: string, data: { name: string; spec_json?: string }): Promise<{ id: string }> {
  return createBookCharacter(bookId, data);
}

export async function updateCharacter(id: string, data: { name?: string; spec_json?: string }): Promise<{ id: string }> {
  return send('PUT', `/api/v1/characters/${id}`, data);
}

export async function deleteCharacter(id: string): Promise<void> {
  return send('DELETE', `/api/v1/characters/${id}`);
}


export interface InspirationCard {
  id: string;
  book_id: string;
  icon: string;
  title: string;
  desc: string;
  type: 'plot' | 'character' | 'world';
  source: 'chief' | 'author';
  adopted: boolean;
  sort_order: number;
}

export async function listInspirations(bookId: string): Promise<InspirationCard[]> {
  return getJson<InspirationCard[]>(`${API_BASE}/api/v1/books/${bookId}/inspirations`);
}

export async function createInspiration(
  bookId: string,
  data: Partial<Omit<InspirationCard, 'id' | 'book_id' | 'source' | 'adopted' | 'sort_order'>>,
): Promise<InspirationCard> {
  return send('POST', `/api/v1/books/${bookId}/inspirations`, data);
}

export async function generateInspirations(bookId: string, direction: string): Promise<{ book_id: string; cards: InspirationCard[] }> {
  return send('POST', `/api/v1/books/${bookId}/inspirations/generate`, { direction });
}

export async function setInspirationAdopted(cardId: string, adopted: boolean): Promise<{ id: string; adopted: boolean }> {
  return send('PATCH', `/api/v1/inspirations/${cardId}`, { adopted });
}

export async function deleteInspiration(cardId: string): Promise<void> {
  return send('DELETE', `/api/v1/inspirations/${cardId}`);
}

export async function updateInspiration(
  id: string,
  data: { title?: string; desc?: string; type?: string; icon?: string },
): Promise<InspirationCard> {
  return send('PUT', `/api/v1/inspirations/${id}`, data);
}

/** 配置上场角色：以该书角色库重建 sim 角色（cast 不重启局面）。 */
export async function setSimCast(
  simId: string,
  characterIds: string[],
): Promise<{ sim_id: string; characters: string[] }> {
  return send('PUT', `/api/v1/sims/${simId}/cast`, { character_ids: characterIds });
}

/** 主笔共创对话历史（按书加载）。 */
export interface ChatHistoryMsg {
  id: string;
  book_id: string;
  role: 'user' | 'assistant';
  content: string;
  ts: number;
}

export async function listChatHistory(bookId: string): Promise<ChatHistoryMsg[]> {
  return getJson<ChatHistoryMsg[]>(`${API_BASE}/api/v1/books/${bookId}/chief/chat_history`);
}

/** 主笔共创对话：SSE 流式读取（event: token → {delta}，event: done 结束）。 */
export async function chiefChat(
  bookId: string,
  messages: Array<{ role: 'user' | 'assistant'; content: string }>,
  onDelta: (delta: string) => void,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/books/${bookId}/chief/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  });
  if (!res.ok || !res.body) throw new Error(`${res.status} ${res.statusText}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // 按 SSE 帧拆分（event: token\ndata: {...}\n\n）
      let idx: number;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const dataLine = frame.split('\n').find((l) => l.startsWith('data:'));
        if (!dataLine) continue;
        try {
          const payload = JSON.parse(dataLine.slice(5).trim()) as { delta?: string };
          if (payload.delta) onDelta(payload.delta);
        } catch {
          /* 忽略坏帧 */
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ---------------------------------------------------------------------------
// 书级记忆（主笔 Agent · 记忆域）
// ---------------------------------------------------------------------------
export type MemoryTopic = 'direction' | 'setting' | 'constraint' | 'history' | 'preference';

export interface BookMemoryMeta {
  id: string;
  book_id: string;
  topic: MemoryTopic;
  content: string;
  source: 'chief' | 'author' | 'audit';
  ts: number;
  created_at?: string;
  updated_at?: string;
}

export async function listMemories(
  bookId: string,
  filters?: { topic?: string; limit?: number },
): Promise<BookMemoryMeta[]> {
  const q = new URLSearchParams();
  if (filters?.topic) q.set('topic', filters.topic);
  if (filters?.limit) q.set('limit', String(filters.limit));
  const qs = q.toString();
  return getJson<BookMemoryMeta[]>(`${API_BASE}/api/v1/books/${bookId}/memories${qs ? `?${qs}` : ''}`);
}

export async function createMemory(
  bookId: string,
  data: { topic: MemoryTopic; content: string; source?: 'author' | 'chief' },
): Promise<{ id: string }> {
  return send('POST', `/api/v1/books/${bookId}/memories`, data);
}

export async function updateMemory(
  id: string,
  data: { topic?: MemoryTopic; content?: string },
): Promise<{ id: string }> {
  return send('PUT', `/api/v1/memories/${id}`, data);
}

export async function deleteMemory(id: string): Promise<void> {
  return send('DELETE', `/api/v1/memories/${id}`);
}

// ---------------------------------------------------------------------------
// 信念账本（v1.5 · 书级 CRUD）
// ---------------------------------------------------------------------------
export type BeliefChannel = 'perceived' | 'told' | 'inferred';

export interface BeliefMeta {
  id: string;
  book_id: string;
  char_id: string;
  fact_id?: string;
  source_event_id?: string;
  channel: BeliefChannel;
  text: string;
  confidence: number;
  edited: boolean;
  ts: number;
  created_at?: string;
  updated_at?: string;
}

export interface BeliefBodySpec {
  char_id: string;
  fact_id?: string;
  source_event_id?: string;
  channel: BeliefChannel;
  text: string;
  confidence?: number;
}

export async function listBookBeliefs(
  bookId: string,
  filters?: { char_id?: string; channel?: string },
): Promise<BeliefMeta[]> {
  const q = new URLSearchParams();
  if (filters?.char_id) q.set('char_id', filters.char_id);
  if (filters?.channel) q.set('channel', filters.channel);
  const qs = q.toString();
  return getJson<BeliefMeta[]>(`${API_BASE}/api/v1/books/${bookId}/beliefs${qs ? `?${qs}` : ''}`);
}

export async function createBelief(bookId: string, data: BeliefBodySpec): Promise<{ id: string }> {
  return send('POST', `/api/v1/books/${bookId}/beliefs`, data);
}

export async function updateBelief(id: string, data: Partial<BeliefBodySpec>): Promise<{ id: string }> {
  return send('PUT', `/api/v1/beliefs/${id}`, data);
}

export async function deleteBelief(id: string): Promise<void> {
  return send('DELETE', `/api/v1/beliefs/${id}`);
}

// ---------------------------------------------------------------------------
// Dashboard 概览（v1.5 · 聚合）
// ---------------------------------------------------------------------------
export interface DashboardData {
  book: {
    id: string;
    title: string;
    genre: string;
    status: string;
    chapter_count: number;
    cover_init: string;
  };
  kpi: {
    chapters_done: number;
    chapters_total: number;
    word_count: number;
    foreshadow_open: number;
    foreshadow_closed: number;
    health: number;
  };
  timeline: Array<{
    chapter_id: string;
    title: string;
    order_no: number;
    tone: string;
    status: 'done' | 'current' | 'draft' | 'planned';
    label: string;
    badge: string;
    word_count: number;
  }>;
  todos: Array<{ severity: 'ok' | 'warn' | 'info' | 'danger'; title: string; desc: string }>;
  quotes: Array<{ text: string; author: string }>;
  heat: number[];
}

export async function fetchDashboard(bookId: string): Promise<DashboardData> {
  return getJson<DashboardData>(`${API_BASE}/api/v1/books/${bookId}/dashboard`);
}

/* ---------- S3 全局张力视图（v1.12） ---------- */

export interface GlobalViewChapter {
  chapter_id: string;
  title: string;
  order_no: number;
  status: 'done' | 'draft' | 'planned' | string;
  scenes: number;
  turns: number;
  tension_avg: number | null;
  tension_peak: number | null;
  trend: string;
  prose_chars: number;
}

export interface GlobalViewDiagnostic {
  rule: string;
  severity: 'high' | 'mid' | 'low' | string;
  deduct: number;
  title: string;
  detail: string;
  evidence: unknown[];
}

export interface GlobalViewForeshadowNode {
  id: string;
  type: string;
  text: string;
  status: string;
  buried_chapter: number | null;
  expected_chapter: number | null;
  related_char_ids: string[];
  overdue: boolean;
}

export interface GlobalView {
  book_id: string;
  title: string;
  sims: number;
  curve: GlobalViewChapter[];
  summary: {
    mean: number | null;
    std: number | null;
    peak_chapter: { chapter_id: string; title: string; order_no: number; tension_avg: number } | null;
    flat_chapters: number[];
    valid: number;
    chapters: number;
    done_chapters: number;
    foreshadows_open: number;
    foreshadows_closed: number;
  };
  diagnostics: GlobalViewDiagnostic[];
  foreshadows: {
    nodes: GlobalViewForeshadowNode[];
    edges: Array<{ from_chapter: number; to_chapter: number; foreshadow_id: string }>;
    overdue: GlobalViewForeshadowNode[];
  };
  structure_score: number;
  barren_scenes: string[];
}

/* ---------- T9 降级可观测（/health） ---------- */

export interface DegradedInfo {
  active: boolean;
  count: number;
  structural: number;
  last_reason: string;
  last_op: string;
  last_at: number;
  kinds: Record<string, number>;
}

export interface HealthStatus {
  status: string;
  degraded: DegradedInfo;
}

/** T9：健康检查（含降级状态）—— 前端据此显示"降级中"横幅。 */
export async function fetchHealth(): Promise<HealthStatus> {
  return getJson<HealthStatus>(`${API_BASE}/health`);
}

/* ---------- S4 涌现产物（剧本 / 回合事件流 · v1.14） ---------- */

export interface ScriptEvent {
  actor?: string;
  kind?: string;
  text?: string;
}

/** 角色思考（分层）：monologue=内心独白；reasoning=动机/推理依据（落正文用）。 */
export interface ScriptThought {
  char?: string;
  char_id?: string;
  monologue?: string;
  reasoning?: string;
  emotion?: string;
}

export interface ScriptTurn {
  turn: number;
  cls: string;
  summary: string;
  tension?: number;
  tension_trend?: string;
  events: ScriptEvent[];
  thoughts: ScriptThought[];
}

/** S4 确定性高光（冲突 / 张力峰值 / 信息密集台词 / 收束回合）。 */
export interface ScriptHit {
  scene_id?: string;
  turn: number;
  kinds: string[];
  tension?: number;
  tension_trend?: string;
  summary?: string;
  quote?: string;
  chars?: string[];
}

export interface SceneScriptResult {
  scene_id: string;
  turns: number;
  markdown: string;
  json: { scene: Record<string, unknown>; turns: ScriptTurn[] };
  hits?: ScriptHit[];
}

/** S4 step2：把确定性高光采纳为灵感卡（source=emergence）。turns 为空 = 全部。 */
export async function adoptEmergenceHits(
  sceneId: string,
  turns?: number[],
): Promise<{ scene_id: string; picked: number; adopted: number }> {
  return send('POST', `/api/v1/scenes/${sceneId}/emergence-hits`, { turns: turns ?? [] });
}

/** S4：单场景剧本产物（回合事件流 + 各角色思考）。 */
export async function fetchSceneScript(
  sceneId: string,
  opts?: { thoughts?: boolean; tension?: boolean },
): Promise<SceneScriptResult> {
  const qs = `?thoughts=${opts?.thoughts === false ? 0 : 1}&tension=${opts?.tension ? 1 : 0}`;
  return getJson<SceneScriptResult>(`${API_BASE}/api/v1/scenes/${sceneId}/script${qs}`);
}

/** S4：剧本 Markdown 下载地址（浏览器直开）。 */
export function sceneScriptUrl(sceneId: string, tension: boolean): string {
  return `${API_BASE}/api/v1/scenes/${sceneId}/script?thoughts=1&tension=${tension ? 1 : 0}`;
}

/** S3 全局张力视图：曲线 + 结构诊断 + 伏笔呼应网络 + 结构分（0-token 派生）。 */
export async function fetchGlobalView(bookId: string): Promise<GlobalView> {
  return getJson<GlobalView>(`${API_BASE}/api/v1/books/${bookId}/global-view`);
}


// ---------------------------------------------------------------------------
// 正文协作工作区（阶段④ · 四角色 + 审计记录）
// ---------------------------------------------------------------------------
export type ProseNoteKind = 'writer' | 'editor' | 'polisher' | 'verifier';
export type ProseNoteStatus = 'pending' | 'approved' | 'rejected';

export interface ProseNote {
  id: string;
  scene_id: string;
  kind: ProseNoteKind;
  status: ProseNoteStatus;
  suggestion: string;
  before: string;
  after: string;
  created_by: string;
  reviewed_at?: string | null;
  ts: number;
  /** 明细 JSON（后端 `repo._prose_note_dict` 已返回）：voice_findings / voice_prior / ai_tone / changes… */
  payload_json?: string | null;
}

/* ---------- A3 反 AI 味（step4/5） ---------- */

export interface AiToneRule {
  rule: string;
  severity: 'warning' | 'violation' | string;
  detail: string;
  evidence: unknown;
}

export interface AiToneReport {
  rules: AiToneRule[];
  counts: { total: number; violation: number; by_rule: Record<string, number> };
  cliches: Array<{ term: string; count: number; evidence: string }>;
  metrics: { dash: number; ellipsis: number; filler_per_100: number; chars: number };
  clean: boolean;
}

export interface AiToneScanResult {
  report: AiToneReport;
  note_status: string;
}

export interface SpotFixChange {
  index?: number;
  before: string;
  after: string;
  rule?: string;
  reason?: string;
  applied?: boolean;
  blocked_reason?: string;
}

export interface SpotFixResult {
  after: string;
  changes: SpotFixChange[];
  accepted: boolean;
  skipped?: string;
  error?: string;
  reason?: string;
  reverted?: boolean;
  hits_before?: number;
  hits_after?: number;
  note_status?: string;
}

/** A3 反 AI 味扫描（0-token 规则）→ 落 editor note（明细在 payload_json.ai_tone）。 */
export async function scanAiTone(sceneId: string, text: string): Promise<AiToneScanResult> {
  return send('POST', `/api/v1/scenes/${sceneId}/prose/ai-tone`, { text });
}

/** A3 定点修复：只改白名单规则命中句；采纳时落 polisher note（after 供"应用"）。 */
export async function spotFixProse(sceneId: string, text: string): Promise<SpotFixResult> {
  return send('POST', `/api/v1/scenes/${sceneId}/prose/spot-fix`, { text });
}

export interface VerifyOpinion {
  foreshadow_updates: Array<{ text: string; status: string; reason: string }>;
  belief_deltas: Array<{ char: string; text: string; channel: string }>;
  causal: string[];
  risks: string[];
}

/** 写手生成正文初稿（无 LLM → text 空串）。 */
export async function generateSceneDraft(sceneId: string): Promise<{ text: string }> {
  return send('POST', `/api/v1/scenes/${sceneId}/prose/draft`);
}

/** 体检员：结构化体检报告。 */
export async function reviewSceneProse(sceneId: string, text: string): Promise<{ report: { issues: Array<{ severity: string; text: string; suggestion: string }>; overall: string }; note_status: string }> {
  return send('POST', `/api/v1/scenes/${sceneId}/prose/review`, { text });
}

/** 润色师：去 AI 味润色（只改写法）。 */
export async function polishSceneProse(sceneId: string, text: string): Promise<{ after: string; summary: string }> {
  return send('POST', `/api/v1/scenes/${sceneId}/prose/polish`, { text });
}

/** 质检员：伏笔/信念/因果对照意见。 */
export async function verifySceneProse(sceneId: string, text: string): Promise<{ opinion: VerifyOpinion; note_status: string }> {
  return send('POST', `/api/v1/scenes/${sceneId}/prose/verify`, { text });
}

/** 审计记录（可追溯）。 */
export async function listProseNotes(sceneId: string): Promise<ProseNote[]> {
  return getJson<ProseNote[]>(`${API_BASE}/api/v1/scenes/${sceneId}/prose/notes`);
}

/** 作者批准（verifier → 记账） / 驳回。 */
export async function approveProseNote(noteId: string): Promise<{ id: string; status: string; bookkeeping: boolean }> {
  return send('POST', `/api/v1/prose-notes/${noteId}/approve`);
}
export async function rejectProseNote(noteId: string): Promise<{ id: string; status: string }> {
  return send('POST', `/api/v1/prose-notes/${noteId}/reject`);
}

/** 作者保存正文 → final_prose 幂等落库。 */
export async function saveSceneProse(sceneId: string, text: string): Promise<{ scene_id: string; unchanged: boolean; word_count: number }> {
  return send('PUT', `/api/v1/scenes/${sceneId}/prose`, { text });
}