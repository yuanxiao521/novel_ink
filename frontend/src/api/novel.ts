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

/* ---------- 灵感池 + 主笔共创（后端补齐 v1.1） ---------- */

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