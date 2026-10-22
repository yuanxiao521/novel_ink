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

/** v1.55 场级必然事件：hard 不可取消（收束时校验）/ soft 只作导演倾向 */
export interface BeatItem {
  id?: string;
  text: string;
  level: "hard" | "soft" | string;
  /** 判定锚点（人物名/物件名/动作词）：事后 0-token 校验用 */
  anchors?: string[];
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
  /** v1.55：主笔定下的本场事件约束 */
  beats?: BeatItem[];
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
  /** v1.55：整场 beats 替换（不给 = 不改） */
  beats?: BeatItem[];
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
  /** 章正文：章正文（prose_md）优先，否则场景聚合 —— v1.46 直写链路 */
  prose: string;
  // ---- v1.46 章级直写字段（后端新增，老字段语义不变）----
  /** 章正文权威存储（chapters.prose_md）：直写模式的真源 */
  prose_md?: string;
  /** 字数（= word_count，新名，口径仍走 text_metrics） */
  words?: number;
  /** 本章字数目标（主笔规划产物，之前从未注入写手 → 已修） */
  word_target?: number;
  tone?: string;
  summary?: string;
  /** v1.50 章级制作单：这一章走直写/涌现/混合（页头徽标用） */
  mode?: string;
  production?: Record<string, unknown>;
}

/* ---------- v1.50 全局脉络（book.brief）与章级制作单 ---------- */

export interface BriefChapter {
  no: number;
  id: string;
  title: string;
  /** 完整章名（标题已含「第 N 章」时不重复前缀） */
  label: string;
  tone: string;
  /** draft 直写 / emerge 涌现 / hybrid 混合 */
  mode: string;
  mode_label: string;
  word_target: number;
  chars: number;
  /** written = 有正文；planned = 还没写 */
  status: string;
  scenes: number;
  has_curve: boolean;
  hook: string;
  must_rehearse: string[];
}

export interface BriefIssue {
  level: "high" | "mid" | "low" | string;
  code: string;
  msg: string;
  where?: string;
}

export interface BookBrief {
  book_id?: string;
  book: { id?: string; title: string; genre: string; synopsis: string };
  chapters: BriefChapter[];
  foreshadows: {
    total: number; buried: number; in_progress: number; closed: number; open: number;
    recovery_rate: number | null;
    overdue: Array<{ id?: string; text?: string; due_chapter?: number; status?: string }>;
  };
  characters: { total: number; entries: Array<{ name: string; appears_in: number[]; worst_gap: number }> };
  facts: { world_states: number; beliefs: number };
  metrics: {
    chapters: number; written: number; planned: number;
    total_words: number; target_sum: number; target_progress: number | null;
    modes: Record<string, number>;
  };
  issues: BriefIssue[];
  counts: { high: number; mid: number; low: number };
  score: number;
  /** 后端渲染好的一页纸（与主笔 prompt 同一份，便于对照） */
  text: string;
}

/* ---------- v1.52 章级生产路径裁决（draft 直写 / emerge 先推演 / hybrid 关键场先演） ---------- */

export interface ProductionSceneRow {
  id: string;
  title: string;
  turns: number;
  finalized: boolean;
  /** 是否属于"必须先推演"的场 */
  required: boolean;
}

export interface ChapterProductionPlan {
  chapter_id: string;
  book_id: string;
  title: string;
  mode: string;
  mode_label: string;
  production: Record<string, unknown>;
  hook: string;
  scenes: ProductionSceneRow[];
  required: string[];
  /** 还差推演的场（空 = 可以写） */
  pending: string[];
  /** draft（可写）| rehearse（先推演）| manual（需先拆场） */
  next_action: string;
  can_write: boolean;
  why: string;
  notes: string[];
  turns_total: number;
}

/** 本章该怎么生产 + 还差什么（写作页据此选路，作者不用记路由）。 */
export async function fetchChapterProductionPlan(
  chapterId: string,
): Promise<ChapterProductionPlan> {
  return getJson<ChapterProductionPlan>(
    `${API_BASE}/api/v1/chapters/${chapterId}/production-plan`);
}

/* ---------- v1.58 前情压缩（分层概要） ---------- */

export interface ChapterDigest {
  chapter_no: number;
  one_line: string;
  events: Array<{ who: string; did: string; where?: string; consequence?: string }>;
  state_changes: Array<{ target: string; from: string; to: string }>;
  knowledge: Array<{ who: string; learned: string; how?: string }>;
  facts_established: string[];
  foreshadow_touched: Array<{ text: string; action: string }>;
  hook: string;
  open_questions: string[];
  new_entities: Array<{ name: string; kind: string; note?: string }>;
  summary_md: string;
  conflicts: Array<{ type?: string; detail?: string }>;
}

export interface VolumeDigest {
  volume_no?: number;
  range?: number[];
  arc?: string;
  characters?: Array<{
    name: string; where?: string; condition?: string;
    knows?: string[]; does_not_know?: string[]; holds?: string[]; stance?: string;
  }>;
  rules?: string[];
  long_foreshadows?: Array<{ text: string; status: string }>;
  turning_points?: string[];
  ending_state?: string;
  summary_md?: string;
}

export interface ChapterPrior {
  chapter_id: string;
  chapter_no: number;
  book: VolumeDigest;
  volume: VolumeDigest;
  recent: ChapterDigest[];
  conflicts: Array<{ chapter_no?: number; type?: string; detail?: string }>;
  volume_no: number;
  volume_title?: string;
  volume_digest_stale?: boolean;
  volume_range: number[];
  volumes: Array<{ no: number; from: number; to: number }>;
  has_any: boolean;
  text: string;
}

/** 本章的前情素材（全书 / 本卷 / 最近两章 + 冲突）—— 写作页「前情概要」面板。 */
export async function fetchChapterPrior(chapterId: string): Promise<ChapterPrior> {
  return getJson<ChapterPrior>(`${API_BASE}/api/v1/chapters/${chapterId}/prior`);
}

/** 重算本章前情概要（后台队列）。 */
export async function regenerateChapterDigest(chapterId: string): Promise<{ job_id: string; status: string }> {
  return send<{ job_id: string; status: string }>(
    "POST", `/api/v1/chapters/${chapterId}/digest/generate`);
}

/** 重算卷级（scope=volume）或全书级（scope=book）前情概要（后台队列）。 */
export async function regenerateBookDigest(
  bookId: string, scope: "volume" | "book" = "volume", volumeNo = 0,
): Promise<{ job_id: string; status: string }> {
  return send<{ job_id: string; status: string }>(
    "POST", `/api/v1/books/${bookId}/digest/generate`, { scope, volume_no: volumeNo });
}

/* ---------- v1.60 分卷（卷实体） ---------- */

export interface BookVolumeRow {
  id: string;
  book_id: string;
  no: number;
  title: string;
  range_from: number;
  range_to: number;
  arc_goal?: string;
  climax?: string;
  /** 本卷章数（接口算的） */
  chapters?: number;
  /** true = 卷表为空，按 volume_size 派生（未落库） */
  derived?: boolean;
  range?: number[];
}

/** 分卷列表（真源是 book_volumes 表；空时派生回退）。 */
export async function fetchBookVolumes(bookId: string): Promise<BookVolumeRow[]> {
  const r = await getJson<{ volumes: BookVolumeRow[] }>(
    `${API_BASE}/api/v1/books/${bookId}/volumes`);
  return r.volumes ?? [];
}

/** 保存分卷（整表替换；校验递增不重叠）。 */
export async function setBookVolumes(
  bookId: string,
  volumes: Array<{
    no?: number; from: number; to: number; title?: string;
    arc_goal?: string; climax?: string;
  }>,
): Promise<{
  volumes: BookVolumeRow[];
  sync?: { assigned: number; unassigned: number[] };
  digests_marked_stale?: number;
}> {
  return send(
    "PUT", `/api/v1/books/${bookId}/volumes`, { volumes });
}

/* ---------- v1.61 正文版本（稿本历史） ---------- */

export interface ChapterRevisionMeta {
  id: string;
  chapter_id: string;
  version: number;
  chars: number;
  source: string;
  created_at: string;
}

/** 稿本历史（version 倒序，不含正文全文）。 */
export async function fetchChapterRevisions(chapterId: string): Promise<ChapterRevisionMeta[]> {
  return getJson<ChapterRevisionMeta[]>(
    `${API_BASE}/api/v1/chapters/${chapterId}/revisions`);
}

export interface ChapterRevisionFull extends ChapterRevisionMeta {
  prose_md: string;
}

/** 取某一版的完整正文。 */
export async function fetchChapterRevision(
  chapterId: string, version: number,
): Promise<ChapterRevisionFull> {
  return getJson<ChapterRevisionFull>(
    `${API_BASE}/api/v1/chapters/${chapterId}/revisions/${version}`);
}

/** 恢复到某一版（覆盖当前正文；恢复动作本身也留一条新版本）。 */
export async function restoreChapterRevision(
  chapterId: string, version: number,
): Promise<{ chapter_id: string; restored_from: number; chars: number; new_version: number }> {
  return send(
    "POST", `/api/v1/chapters/${chapterId}/revisions/${version}/restore`,
    { confirm: true },
  );
}

/** 全局脉络 + 骨架体检（0-token，零 LLM）。 */
export async function fetchBookBrief(bookId: string): Promise<BookBrief> {
  return getJson<BookBrief>(`${API_BASE}/api/v1/books/${bookId}/brief`);
}

/** 写章级制作单（主笔裁决 / 作者覆盖）：只改结构，不碰正文。 */
export async function setChapterProduction(
  chapterId: string,
  body: { mode?: string; production?: Record<string, unknown> },
): Promise<{ chapter_id: string; mode: string; production: Record<string, unknown> }> {
  return send<{ chapter_id: string; mode: string; production: Record<string, unknown> }>(
    "PUT", `/api/v1/chapters/${chapterId}/production`, body);
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

/* ---------- 跨书搜索（v1.34） ---------- */

export interface SearchHit {
  kind: 'book' | 'chapter' | 'scene' | 'character' | 'foreshadow' | string;
  id: string;
  label: string;
  subtitle: string;
  book_id: string;
  chapter_id?: string;
  /** 前端可直接 nav() 的落点（已带 ?book= / &chapter=） */
  path: string;
}

export interface SearchResult {
  q: string;
  hits: SearchHit[];
  counts: Record<string, number>;
}

/** 跨书搜索：书名 / 章节 / 场景 / 角色 / 伏笔（字面包含匹配）。 */
export async function searchEverything(q: string, limit = 8): Promise<SearchResult> {
  return getJson<SearchResult>(`${API_BASE}/api/v1/search?q=${encodeURIComponent(q)}&limit=${limit}`);
}

/* ---------- 逐章校对（v1.35 成书收口 C） ---------- */

export interface CollationScene {
  scene_id: string;
  title: string;
  word_count: number;
  finalized: boolean;
  preview: string;
  goal: string;
}

export interface CollationChapter {
  chapter_id: string;
  order_no: number;
  title: string;
  tone: string;
  summary: string;
  word_target: number;
  word_count: number;
  scene_count: number;
  done: boolean;
  scenes: CollationScene[];
}

export interface Collation {
  book_id: string;
  title: string;
  chapters: CollationChapter[];
  kpi: {
    chapters_total: number;
    chapters_done: number;
    word_count: number;
    unfinished_scenes: number;
    scene_total: number;
  };
}

/** 逐章校对数据：每章场景清单（字数/定稿状态/预览）+ 全书统计。 */
export async function fetchCollation(bookId: string): Promise<Collation> {
  return getJson<Collation>(`${API_BASE}/api/v1/books/${bookId}/collation`);
}

/* ---------- 整书体检（v1.38 · 0-token，零 LLM） ---------- */

export interface AuditDiag {
  rule: string;
  severity: string;
  deduct: number;
  title: string;
  detail: string;
  evidence: unknown[];
}

export interface VoiceDrift {
  name: string;
  scenes_count: number;
  baseline_len: number;
  baseline_particles: string[];
  scenes: Array<{ scene_id: string; order: number; title: string; avg_len: number; particles: string[]; len_delta: number }>;
  flagged: Array<{ kind: string; scene_id: string; order: number; title: string; detail: string }>;
}

export interface TimelineRewind {
  key: string;
  name: string;
  detail: string;
  ws_id: string;
  scene_no: number;
  from: number;
  to: number;
  edited: boolean;
}

export interface HealthReport {
  book_id: string;
  title: string;
  structure: { score: number | null; diagnostics: AuditDiag[]; summary: Record<string, unknown> };
  voice: { characters: VoiceDrift[]; flagged: number; scenes_checked: number };
  timeline: { flows: Array<Record<string, unknown>>; rewinds: TimelineRewind[]; keys_checked: number };
  checked_at: number;
}

/** 整书体检：结构诊断 + 口音漂移 + 时间线回放。 */
export async function fetchHealthReport(bookId: string): Promise<HealthReport> {
  return getJson<HealthReport>(`${API_BASE}/api/v1/books/${bookId}/health-report`);
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
  /** v1.53 角色分层：主角/关键配角/反派进涌现回合，次要/背景只作在场背景 */
  role_tier?: string;
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
export interface TierAssignment {
  id: string;
  name: string;
  role_tier: string;
  role_tier_label: string;
  current: string;
  why: string;
}

export interface TierInferResult {
  book_id: string;
  assignments: TierAssignment[];
  counts: Record<string, number>;
  applied?: number;
  total?: number;
  skipped?: string;
  error?: string;
}

/** 让主笔给存量角色卡分档（apply=false 只出预览；apply=true 才落库）。 */
export async function inferCharacterTiers(bookId: string, apply = false): Promise<TierInferResult> {
  const res = await fetch(
    `${API_BASE}/api/v1/books/${bookId}/characters/tiers/infer?apply=${apply ? "true" : "false"}`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as TierInferResult;
}

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

export async function updateCharacter(id: string, data: { name?: string; spec_json?: string; role_tier?: string }): Promise<{ id: string }> {
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

/* ---------- A2 质量回环 ---------- */

export interface QualityWeakPoint {
  dim?: string;
  evidence?: string;
  why?: string;
}

export interface QualityReport {
  total: number;
  llm_total?: number;
  scores: Record<string, number>;
  weak_points?: QualityWeakPoint[];
  hard?: Record<string, unknown>;
}

export interface QualityChange {
  before?: string;
  after?: string;
  rule?: string;
  applied?: boolean;
}

export interface QualityLoopResult {
  after: string;
  accepted: boolean;
  route?: string;
  rewrites?: number;
  skipped?: string;
  reason?: string;
  error?: string;
  reverted?: boolean;
  before?: QualityReport;
  after_score?: QualityReport;
  changes?: QualityChange[];
  note_status?: string;
}

/** A2：质量回环（自评 → 达标不动 / 不达标重写一次 → 三闸复检）。 */
export async function runQualityLoop(sceneId: string, text: string): Promise<QualityLoopResult> {
  return send('POST', `/api/v1/scenes/${sceneId}/prose/quality-loop`, { text });
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

export interface LlmInfo {
  configured: boolean;
  ok: boolean;
  model: string;
  failures: number;
  last_status: number;
  last_error: string;
}

/** v1.45（B51）：后台任务队列的活性——worker 死了要立刻看得见，别让人干等超时。 */
export interface QueueInfo {
  alive: boolean;
  status: 'ok' | 'no-worker' | 'stuck' | 'unknown' | string;
  heartbeat_age?: number | null;
  heartbeat_ttl?: number;
  queued?: number;
  in_progress?: number;
  stuck?: number;
  error?: string;
}

export interface HealthStatus {
  status: string;
  degraded: DegradedInfo;
  /** B32：LLM 可用性（模型名/账号异常时 ok=false）—— 必须可见，否则正文会静默走回退 */
  llm?: LlmInfo;
  /** v1.45（B51）：后台队列活性（排批 / 审稿会 / 质量回环都靠它） */
  queue?: QueueInfo;
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
  /** 本回合成文段落（TurnArchive.prose）—— 以前只落在库里，界面看不到（已补渲染）。 */
  prose?: string;
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

/** 主笔生成角色卡（空 cast 一键补齐；作者可再编辑）。 */
export async function generateBookCharacters(
  bookId: string,
  direction = '',
): Promise<{ book_id: string; created: number }> {
  return send('POST', `/api/v1/books/${bookId}/characters/generate`, { direction });
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
export type ProseNoteKind = 'writer' | 'editor' | 'polisher' | 'verifier' | 'tool' | 'bookkeeping' | string;
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

// ---------------------------------------------------------------------------
// P2 · 正文协作副驾（Agent 工具层 + 段落批注 + 责编对话）
// 按钮与对话走**同一条工具链**：callAgentTool 与 editorChat 最终都落到后端 ToolExecutor
// ---------------------------------------------------------------------------

export type ToolSideEffect = 'read' | 'write' | 'destructive';

export interface AgentToolDecl {
  name: string;
  desc: string;
  side_effect: ToolSideEffect;
  params: string[];
  optional: string[];
  needs_confirm: boolean;
  group: string;
}

export interface AgentAction {
  tool: string;
  args: Record<string, unknown>;
  why?: string;
}

export interface AgentExecuted {
  tool: string;
  ok: boolean;
  side_effect: ToolSideEffect | string;
  note_id?: string;
  error?: string;
  why?: string;
  data?: Record<string, unknown>;
}

export interface PerceptSummary {
  who: { id: string; name: string };
  scope: string;
  scene_id: string;
  sources: string[];
  counts: Record<string, number>;
  dropped: Array<{ src?: string; reason: string }>;
}

export interface EditorChatResult {
  reply: string;
  actions: AgentAction[];
  executed: AgentExecuted[];
  percept: PerceptSummary;
}

export type AnnotationStatus = 'open' | 'handled' | 'dismissed';

export interface ProseAnnotation {
  id: string;
  scene_id: string;
  para_index: number;
  quote: string;
  note: string;
  status: AnnotationStatus;
  created_by: string;
  handled_by_note_id?: string;
  handled_at?: string | null;
  ts: number;
  created_at?: string;
  updated_at?: string;
}

/** 工具清单（前端按 group 分组、按 side_effect 决定是否弹确认）。 */
export async function listAgentTools(): Promise<AgentToolDecl[]> {
  const r = await getJson<{ tools: AgentToolDecl[] }>(`${API_BASE}/api/v1/agent/tools`);
  return r.tools;
}

/** 作者直调工具（= 界面按钮）：与责编对话同一条链。409（抛错）= 需确认 / 被拒。 */
export async function callAgentTool(
  sceneId: string,
  tool: string,
  args: Record<string, unknown>,
  confirm = false,
): Promise<AgentExecuted> {
  const res = await fetch(`${API_BASE}/api/v1/scenes/${sceneId}/tools/${encodeURIComponent(tool)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ args, confirm }),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = (await res.json()) as { detail?: string };
      if (j?.detail) detail = j.detail;
    } catch {
      /* 忽略解析失败 */
    }
    throw new Error(detail);
  }
  return (await res.json()) as AgentExecuted;
}

/** 责编对话：感知包 → 计划 → 执行 → 汇报。 */
export async function editorChat(sceneId: string, message: string, text: string): Promise<EditorChatResult> {
  return send<EditorChatResult>('POST', `/api/v1/scenes/${sceneId}/agent/chat`, { message, text, who: 'author' });
}



/* ---------- 责编对话（SSE 流式 · v1.39"知道它在干什么"） ---------- */

/** 责编工作过程的阶段事件（后端 editor_chat_stream 逐阶段下发）。 */
export type EditorStage =
  | { event: 'percept'; data: PerceptSummary }
  | { event: 'plan'; data: { reply: string; actions: Array<{ tool: string; args?: Record<string, unknown>; why?: string }> } }
  | { event: 'tool_start'; data: { tool: string; why?: string } }
  | { event: 'tool_done'; data: { tool: string; ok: boolean; summary?: string; error?: string; result?: AgentExecuted } }
  | { event: 'reply'; data: { reply: string } }
  | { event: 'done'; data: { percept: PerceptSummary; executed: AgentExecuted[] } }
  | { event: 'error'; data: { error: string } };

/** SSE 读取器（v1.43 抽出）：POST → 逐帧回调 {event,data}；非 2xx 抛错（带后端 detail）。 */
async function streamPost(
  url: string,
  body: Record<string, unknown>,
  onStage: (s: { event: string; data: unknown }) => void,
): Promise<void> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    let detail = `${res.status}`;
    try { const j = (await res.json()) as { detail?: string }; if (j?.detail) detail = j.detail; } catch { /* 忽略 */ }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop() ?? '';
    for (const part of parts) {
      const evLine = part.split('\n').find((l) => l.startsWith('event: '));
      const dataLine = part.split('\n').find((l) => l.startsWith('data: '));
      if (!evLine || !dataLine) continue;
      const event = evLine.slice(7).trim();
      try { onStage({ event, data: JSON.parse(dataLine.slice(6)) }); } catch { /* 跳过坏帧 */ }
    }
  }
}

/** 流式责编对话：逐阶段回调 onStage，结束返回最终结果（供状态归档）。 */
export async function editorChatStream(
  sceneId: string,
  message: string,
  text: string,
  onStage: (s: EditorStage) => void,
): Promise<void> {
  await streamPost(
    `${API_BASE}/api/v1/scenes/${sceneId}/agent/chat/stream`,
    { message, text, who: 'author' },
    (s) => onStage(s as EditorStage),
  );
}

/* ---------- 按钮直调工具（SSE · v1.43"复杂按钮也看得见过程"） ---------- */

/** 按钮工具的阶段事件：与对话同一词汇，外加 tick（长工具的耗时心跳）。 */
export type ToolRunStage =
  | { event: 'tool_start'; data: { tool: string; why?: string } }
  | { event: 'tick'; data: { tool: string; elapsed: number } }
  | { event: 'tool_done'; data: { tool: string; ok: boolean; denied?: boolean; summary?: string; error?: string; elapsed?: number; result?: AgentExecuted } }
  | { event: 'done'; data: { executed: AgentExecuted[] } }
  | { event: 'error'; data: { error: string } };

/**
 * 按钮直调工具的流式版：作者点「体检 / 质检 / 审稿会」等，也能看到
 * 开始 → 跑了多久 → 查出几条 → 明细。denied=true 时前端弹确认后带 confirm 重试。
 */
export async function toolRunStream(
  sceneId: string,
  tool: string,
  args: Record<string, unknown>,
  onStage: (s: ToolRunStage) => void,
  confirm = false,
): Promise<void> {
  await streamPost(
    `${API_BASE}/api/v1/scenes/${sceneId}/agent/run/stream`,
    { tool, args, confirm },
    (s) => onStage(s as ToolRunStage),
  );
}


/* ---------- 章级直写（v1.46 · 直写链路）：章是最小可写单位，场是可选细分 ---------- */
/* 注意：ChapterProse / fetchChapterProse 复用文件上方既有定义（只做了字段扩展），
   不要在这里再定义一份——重复实现会让"改了一处另一处没生效"（B45 的教训）。 */

/** 保存章正文（写入 chapters.prose_md，直写链路的唯一真源）。 */
export async function saveChapterProse(
  chapterId: string, text: string,
): Promise<{ chapter_id: string; saved: boolean; words: number; unchanged?: boolean; bookkeeping?: string }> {
  return send('PUT', `/api/v1/chapters/${chapterId}/prose`, { text });
}

/** 补齐章摘要 + 各场设定（只补空字段，不覆盖作者手写）。 */
export async function generateChapterSettings(
  chapterId: string, force = false,
): Promise<{ filled: number; skipped?: string; error?: string; scenes?: number; chapter_summary?: boolean; scenes_generated?: number }> {
  return send('POST', `/api/v1/chapters/${chapterId}/settings/generate`, { force });
}

/** 章级巡检（体检 ∥ 质检 → 评审 → 裁决 → 记账）：后台队列，返回 job_id 供轮询。 */
export async function enqueueChapterInspect(chapterId: string): Promise<{ job_id: string; kind: string; status: string }> {
  return send('POST', `/api/v1/chapters/${chapterId}/inspect`, {});
}

/** 章级记账 + 复检（手动重跑；正常保存时会自动入队）。 */
export async function chapterBookkeeping(
  chapterId: string, text: string,
): Promise<{ written: number; foreshadow: number; beliefs: number; states: number; risks: number; skipped?: string; error?: string }> {
  return send('POST', `/api/v1/chapters/${chapterId}/bookkeeping`, { text });
}

/**
 * **章级直写**流式（v1.46）：按章字数目标分段写满一章，逐 token 回调。
 * 与 draftProseStream 的区别：那是「写一段（150-220 字）」，这是「写完一章」。
 */
export async function chapterDraftStream(
  chapterId: string,
  onToken: (delta: string) => void,
  onDone?: (text: string, chars: number) => void,
): Promise<void> {
  let err = '';
  await streamPost(`${API_BASE}/api/v1/chapters/${chapterId}/prose/draft/stream`, {}, (s) => {
    if (s.event === 'token') onToken(String((s.data as { delta?: string }).delta ?? ''));
    else if (s.event === 'done') {
      const d = s.data as { text?: string; chars?: number };
      onDone?.(String(d.text ?? ''), Number(d.chars ?? 0));
    } else if (s.event === 'error') err = String((s.data as { error?: string }).error ?? '章级直写出错');
  });
  if (err) throw new Error(err);
}


/* ---------- 写手流式（v1.44）：逐 token 写进中间正文区 ---------- */

/**
 * 写手初稿**流式**版：逐 token 回调，作者能看到正文一个字一个字长出来。
 * 非流式的 POST /prose/draft 仍保留（工具链/责编对话走那条）。
 * 收尾的 writer 审计由后端负责（含流式中断时的部分稿）。
 */
export async function draftProseStream(
  sceneId: string,
  onToken: (delta: string) => void,
  onDone?: (text: string, chars: number) => void,
): Promise<void> {
  let err = '';
  await streamPost(`${API_BASE}/api/v1/scenes/${sceneId}/prose/draft/stream`, {}, (s) => {
    if (s.event === 'token') onToken(String((s.data as { delta?: string }).delta ?? ''));
    else if (s.event === 'done') {
      const d = s.data as { text?: string; chars?: number };
      onDone?.(String(d.text ?? ''), Number(d.chars ?? 0));
    } else if (s.event === 'error') err = String((s.data as { error?: string }).error ?? '写手流式出错');
  });
  if (err) throw new Error(err);
}


/* ---------- 后台任务队列（v1.41）：长 LLM 操作与连接解耦 ---------- */

export interface JobState {
  job_id: string;
  kind: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  stages: Array<{ text: string; detail?: string; ts: number }>;
  result?: Record<string, unknown>;
  error?: string;
  created_at?: number;
}

/** 入队长 LLM 任务：<100ms 返回 job_id（book 级）。 */
export async function enqueueBookJob(
  bookId: string,
  kind: 'plan_batch' | 'review_meeting' | 'quality_loop',
  body: { direction?: string; batch_size?: number; batch_from?: number; text?: string; scene_id?: string } = {},
): Promise<{ job_id: string; kind: string; status: string }> {
  return send('POST', `/api/v1/books/${bookId}/jobs/${kind}`, body);
}

/** 轮询任务进度/结果。 */
export async function fetchJobStatus(jobId: string): Promise<JobState> {
  return getJson<JobState>(`${API_BASE}/api/v1/jobs/${jobId}`);
}

/** 轮询直到任务完成/失败（间隔 intervalMs，上限 maxMs）。 */
export async function pollJob(
  jobId: string,
  onTick?: (st: JobState) => void,
  opts: { intervalMs?: number; maxMs?: number } = {},
): Promise<JobState> {
  const interval = opts.intervalMs ?? 2500;
  const max = opts.maxMs ?? 300000;
  const start = Date.now();
  for (;;) {
    const st = await fetchJobStatus(jobId);
    onTick?.(st);
    if (st.status === 'done' || st.status === 'failed') return st;
    if (Date.now() - start > max) throw new Error('任务超时（' + Math.round(max / 1000) + 's 未完成）');
    await new Promise((r) => setTimeout(r, interval));
  }
}

export async function listAnnotations(sceneId: string, status = ''): Promise<ProseAnnotation[]> {
  const q = status ? `?status=${status}` : '';
  return getJson<ProseAnnotation[]>(`${API_BASE}/api/v1/scenes/${sceneId}/annotations${q}`);
}

export async function createAnnotation(
  sceneId: string,
  body: { note: string; para_index: number; quote: string },
): Promise<ProseAnnotation> {
  return send<ProseAnnotation>('POST', `/api/v1/scenes/${sceneId}/annotations`, body);
}

export async function patchAnnotation(
  id: string,
  patch: { status?: AnnotationStatus; note?: string; handled_by_note_id?: string },
): Promise<ProseAnnotation> {
  return send<ProseAnnotation>('PATCH', `/api/v1/annotations/${id}`, patch);
}

export async function deleteAnnotation(id: string): Promise<void> {
  return send<void>('DELETE', `/api/v1/annotations/${id}`);
}

// ---------------------------------------------------------------------------
// A+ · 任务总线（L3）：请求彩排 / 曝光 / 裁决 / 重写 —— 只传请求与裁决，不传状态
// ---------------------------------------------------------------------------

export interface AgentTask {
  id: string;
  book_id: string;
  scene_id: string;
  from_agent: string;
  to_agent: string;
  kind: 'rehearsal' | 'expose' | 'adjudicate' | 'rewrite' | string;
  kind_label: string;
  goal: string;
  status: 'submitted' | 'working' | 'input-required' | 'completed' | 'failed' | string;
  input_ref: Record<string, unknown>;
  artifact_ref: Record<string, unknown>;
  ts: number;
  created_at?: string;
  updated_at?: string;
}

export interface AgentSpecInfo {
  id: string;
  name: string;
  scope: string;
  reads: string[];
  writes: string[];
  tools: string[];
  can_initiate_tasks: boolean;
  needs_confirm: string[];
}

export async function listAgentTasks(sceneId: string, status = ''): Promise<AgentTask[]> {
  const q = status ? `?status=${status}` : '';
  return getJson<AgentTask[]>(`${API_BASE}/api/v1/scenes/${sceneId}/agent/tasks${q}`);
}

export async function createAgentTask(
  sceneId: string,
  body: { kind?: string; goal?: string; to_agent?: string; from_agent?: string },
): Promise<AgentTask> {
  const res = await fetch(`${API_BASE}/api/v1/scenes/${sceneId}/agent/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: 'rehearsal', from_agent: 'author', ...body }),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = (await res.json()) as { detail?: string };
      if (j?.detail) detail = j.detail;
    } catch { /* 忽略 */ }
    throw new Error(detail);
  }
  return (await res.json()) as AgentTask;
}

export async function patchAgentTask(taskId: string, status: string, note = ''): Promise<AgentTask> {
  return send<AgentTask>('PATCH', `/api/v1/agent/tasks/${taskId}`, { status, note });
}

export async function taskMessages(taskId: string): Promise<Array<{ id: string; role: string; from_agent: string; to_agent: string; content_json: string; ts: number }>> {
  return getJson(`${API_BASE}/api/v1/agent/tasks/${taskId}/messages`);
}


// ---------------------------------------------------------------------------
// C 批 · 彩排建议（主笔）+ 素材使用率（启发式）
// ---------------------------------------------------------------------------

export interface RehearsalScene {
  scene_id: string;
  scene_title: string;
  chapter_id: string;
  chapter_title: string;
  chapter_no: number;
  rehearsed_turns: number;
  suggested: boolean;
  score: number;
  reasons: string[];
}

export interface RehearsalPlan {
  book_id: string;
  method: string;
  threshold: number;
  scenes: RehearsalScene[];
  summary: { scenes: number; suggested: number; rehearsed: number };
}

export interface MaterialUsageScene {
  scene_id: string;
  scene_title: string;
  prose_chars: number;
  adopted: number;
  referenced: number;
  items: Array<{ id: string; title: string; referenced: boolean; match: string }>;
}

export interface MaterialUsage {
  book_id: string;
  method: string;
  adopted_cards: Array<{ id: string; title: string }>;
  scenes: MaterialUsageScene[];
  summary: { adopted: number; referenced_slots: number; slot_total: number; rate: number | null };
}

/** 彩排建议（0-token 重头戏判定）：主笔视角，作者可覆盖。 */
export async function fetchRehearsalPlan(bookId: string): Promise<RehearsalPlan> {
  return getJson<RehearsalPlan>(`${API_BASE}/api/v1/books/${bookId}/rehearsal-plan`);
}

/** 素材使用率：演了的素材写的时候用上没有（启发式文本命中）。 */
export async function fetchMaterialUsage(bookId: string): Promise<MaterialUsage> {
  return getJson<MaterialUsage>(`${API_BASE}/api/v1/books/${bookId}/material-usage`);
}

export interface ConstraintTable {
  book_id: string;
  source: string;
  direction: string;
  worldview: { premise?: string; rules_text?: string; background?: string };
  hard_rules: Array<{ concept: string; constraint: string; keywords: string[] }>;
  constraints: string[];
  preferences: string[];
  settings: string[];
  counts: Record<string, number>;
  dropped: Array<{ src: string; reason: string }>;
}

/** 书级约束表（B 批）：全员读单一来源，0-token 派生。 */
export async function fetchConstraints(bookId: string): Promise<ConstraintTable> {
  return getJson<ConstraintTable>(`${API_BASE}/api/v1/books/${bookId}/constraints`);
}

export interface WorldStateMeta {
  id: string;
  book_id: string;
  kind: string; // character | item | time | term | numeric
  name: string;
  key: string;
  value: string;
  previous_value: string;
  scene_no: number;
  source_event_id: string;
  edited: boolean;
  created_at: string;
  updated_at: string;
}

/** S1 世界状态账本只读浏览（设定页）；kind 可选过滤。 */
export async function fetchWorldStates(bookId: string, kind?: string): Promise<WorldStateMeta[]> {
  const q = kind ? `?kind=${encodeURIComponent(kind)}` : '';
  const r = await getJson<{ states: WorldStateMeta[] }>(`${API_BASE}/api/v1/books/${bookId}/world-states${q}`);
  return r.states ?? [];
}

/** 作者手写世界状态（edited=True，质检不再覆盖）；同 (kind,key) 已有行则更新。 */
export async function createWorldState(bookId: string, body: {
  kind: string; name: string; value: string; attr?: string; key?: string; scene_no?: number;
}): Promise<{ id: string }> {
  return send('POST', `/api/v1/books/${bookId}/world-states`, body);
}

/** 作者修改世界状态（落库置 edited=True）。 */
export async function updateWorldState(wsId: string, body: {
  kind: string; name: string; value: string; key?: string; scene_no?: number;
}): Promise<{ id: string }> {
  return send('PUT', `/api/v1/world-states/${wsId}`, body);
}

export async function deleteWorldState(wsId: string): Promise<void> {
  return send('DELETE', `/api/v1/world-states/${wsId}`);
}

export async function listAgentSpecs(): Promise<{ specs: AgentSpecInfo[]; write_matrix: Array<{ target: string; owner: string; note: string; enforced: boolean }> }> {
  return getJson(`${API_BASE}/api/v1/agent/specs`);
}

// ---------------------------------------------------------------------------
// v1.49 · 记忆检索（RAG）：前情 / 相关角色 / 世界观 / 未回收伏笔
// 为什么前端也要能读到：作者必须能看见「写手这次到底记住了什么」，
// 否则"它为什么不按前文写"永远是个黑盒。写作页「记忆」面板与写手 prompt
// 用的是**同一个后端接口**（/chapters/{id}/recall），口径不分叉。
// ---------------------------------------------------------------------------

export interface RecallItem {
  id: string;
  kind: string;
  chapter_no: number;
  title: string;
  text: string;
  score: number;
}

export interface ChapterRecall {
  prev: RecallItem[];
  /** v1.62：知晓范围——谁在哪一章知道了什么（后文写他不知情就是硬伤） */
  knowledge: RecallItem[];
  characters: RecallItem[];
  world: RecallItem[];
  foreshadows: RecallItem[];
  /** keyword（中文关键词）| hybrid（向量+关键词）| fallback（全靠兜底直取） */
  mode: string;
  /** 五段里由检索命中的段数；0 = 全靠兜底直取 */
  from_search: number;
  query: string;
  counts: Record<string, number>;
  chars: number;
  text: string;
  book_id?: string;
}

export interface MemoryStats {
  book_id: string;
  chunks: number;
  by_kind: Record<string, number>;
  vectors: number;
  indexed_at: number;
  embedding?: { available: boolean; model?: string; dim?: number; reason?: string };
}

/** 本章会注入写手 prompt 的记忆素材（写作页「记忆」面板数据源）。 */
export async function fetchChapterRecall(chapterId: string): Promise<ChapterRecall> {
  return getJson<ChapterRecall>(`${API_BASE}/api/v1/chapters/${chapterId}/recall`);
}

/** 记忆索引统计 + 向量通道是否可用（为什么是纯关键词，这里能看出来）。 */
export async function fetchBookMemoryStats(bookId: string): Promise<MemoryStats> {
  return getJson<MemoryStats>(`${API_BASE}/api/v1/books/${bookId}/memory/stats`);
}

/** 重建本书记忆索引（后台任务）：返回 job_id，用 pollJob 取进度。 */
export async function reindexBookMemory(bookId: string): Promise<{ job_id: string; kind: string; status: string }> {
  const res = await fetch(`${API_BASE}/api/v1/books/${bookId}/memory/reindex`, { method: "POST" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as { job_id: string; kind: string; status: string };
}

/** 记忆检索（对话调试用）：按查询取片段，kinds 逗号分隔。 */
export async function searchMemory(
  bookId: string, q: string, kinds = "", topK = 8,
): Promise<{ query: string; mode: string; hits: RecallItem[]; count: number }> {
  const params = new URLSearchParams({ q, top_k: String(topK) });
  if (kinds) params.set("kinds", kinds);
  return getJson(`${API_BASE}/api/v1/books/${bookId}/memory/search?${params.toString()}`);
}
/* ---------- 章级副驾（v1.65 · 两个页面合并：责编按整章干活） ---------- */

/** 章级责编对话（SSE）：事件词汇与场级完全一致，前端复用同一张过程卡。 */
export async function chapterEditorChatStream(
  chapterId: string,
  message: string,
  text: string,
  onStage: (s: EditorStage) => void,
): Promise<void> {
  await streamPost(
    `${API_BASE}/api/v1/chapters/${chapterId}/agent/chat/stream`,
    { message, text, who: 'author' },
    (s) => onStage(s as EditorStage),
  );
}

/** 章级按钮直调工具（SSE）：与对话同一事件词汇（tool_start / tick / tool_done / done）。 */
export async function chapterToolRunStream(
  chapterId: string,
  tool: string,
  args: Record<string, unknown>,
  onStage: (s: ToolRunStage) => void,
  confirm = false,
): Promise<void> {
  await streamPost(
    `${API_BASE}/api/v1/chapters/${chapterId}/agent/run/stream`,
    { tool, args, confirm },
    (s) => onStage(s as ToolRunStage),
  );
}

/** 章级审校记录（体检/质检/润色/AI 味/并入 的留痕），ts 倒序。 */
export async function fetchChapterNotes(
  chapterId: string, limit = 50,
): Promise<{ chapter_id: string; notes: ProseNote[]; count: number }> {
  return getJson(`${API_BASE}/api/v1/chapters/${chapterId}/agent/notes?limit=${limit}`);
}

/** 把已推演的场正文并入章正文（破坏性：confirm=true；覆盖前旧正文自动存版本）。 */
export async function mergeChapterScenes(
  chapterId: string, sceneId = "", confirm = true,
): Promise<{ chapter_id: string; merged: number; chars: number; skipped?: string;
             scenes?: Array<{ scene_id: string; title: string; chars: number }> }> {
  return send(`POST`, `/api/v1/chapters/${chapterId}/merge-scenes`, { scene_id: sceneId, confirm });
}



/** 章级骨架遵循度审计（v1.67 · 只读）：0-token 判定 + LLM 偏离判定，不改正文。 */
export interface ChapterAuditItem {
  kind?: string;
  detail: string;
  suggest?: string;
  severity?: string;
  /** v1.70：这条建议的作用范围（chapter/scene/para）——按段改的输入 */
  scope_hint?: string;
}

export interface ChapterAudit {
  chapter_id: string;
  chapter_no: number;
  goal: { chapter: string; tension_curve: string; volume: string; climax: string };
  checks: Array<{ code: string; level: string; detail: string; suggest: string }>;
  deviation: { verdict: string; score: number | null; items: ChapterAuditItem[]; keep: string[] } | null;
  skipped?: string;
  rendered?: string;
  note_id?: string;
}

export async function auditChapter(chapterId: string): Promise<ChapterAudit> {
  return send("POST", `/api/v1/chapters/${chapterId}/audit`, {});
}


/* ---------- 方案②：按建议改稿（候选）→ 采纳（写库+留版本）（v1.69） ---------- */

export interface ChapterRewriteDiff {
  scope: string;
  para_index?: number;
  before_chars: number;
  after_chars: number;
  changed_ratio: number;
  wide_change?: boolean;
  total_chars: number;
}

export interface ChapterRewriteResult {
  chapter_id?: string;
  note_id?: string;
  summary?: string;
  kept?: string[];
  after?: string;
  diff?: ChapterRewriteDiff;
  based_on?: string;
  cache?: { key: string; hit: boolean; ttl: number };
  error?: string;
}

/** 按一条审校建议产候选（不落库；采纳才写正文）。 */
export async function rewriteChapter(
  chapterId: string, instruction: string, scope = "chapter", paraIndex = 0,
): Promise<ChapterRewriteResult> {
  return send("POST", `/api/v1/chapters/${chapterId}/rewrite`,
              { instruction, scope, para_index: paraIndex });
}

export interface ApplyNoteResult {
  note_id: string;
  target?: string;
  prev_revision?: number;
  chars?: number;
  already_applied?: boolean;
}

/** 采纳候选：写进正文并自动存版本（需 confirm；based_on 过期会被拒）。 */
export async function applyProseNote(noteId: string, basedOn = ""): Promise<ApplyNoteResult> {
  return send("POST", `/api/v1/prose-notes/${noteId}/apply`, { confirm: true, based_on: basedOn });
}
