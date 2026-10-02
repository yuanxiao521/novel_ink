import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { fetchBookTree, fetchGlobalView, listBooks, listBookCharacters } from '../api/novel';
import type { BookMeta, BookTree, GlobalView } from '../api/novel';
import { API_BASE } from '../types/types';

/* ==========================================================================
   WorkspaceContext —— 全局上下文锚（书 / 章 / 场景）
   目标：所有工作页共享同一上下文；三段落点写进 URL；刷新不丢。
   优先级：路径参数 > ?query > localStorage > 第一本书
   ========================================================================== */

export type StepKey = 'maestro' | 'director' | 'studio' | 'dashboard' | 'settings';

export interface SceneOption {
  id: string;
  title: string;
  chapter_id: string;
  stage_desc?: string;
  goal?: string;
  characters: number;
}

export interface ChapterOption {
  id: string;
  title: string;
  order_no: number;
  scenes: SceneOption[];
}

export interface StepLink {
  label: string;
  to: string;
}

export interface Blocker {
  text: string;
  to: string;
}

export interface WorkspaceStatus {
  outline: boolean;
  turns: boolean;
  prose: boolean;
  cast: boolean;
}

interface WorkspaceValue {
  books: BookMeta[];
  bookId: string;
  book: BookMeta | null;
  tree: BookTree | null;
  globalView: GlobalView | null;
  chapters: ChapterOption[];
  chapterId: string;
  sceneId: string;
  scene: SceneOption | null;
  scenes: SceneOption[];
  characterCount: number;
  status: WorkspaceStatus;
  setBook: (id: string) => void;
  setChapter: (id: string) => void;
  setScene: (id: string) => void;
  reload: () => void;
  refreshBooks: () => void;
  stepLink: (from: StepKey, dir: 'prev' | 'next') => StepLink | null;
  blocker: (from: StepKey) => Blocker | null;
}

const WorkspaceContext = createContext<WorkspaceValue | null>(null);
const LS_KEY = 'mk.workspace';

/** 从路径取深链场景：/director/:id、/studio/:id */
function pathScene(pathname: string): string {
  const m = pathname.match(/^\/(?:director|studio)\/([^/?#]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

function readStored(): { bookId: string; chapterId: string; sceneId: string } {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return { bookId: '', chapterId: '', sceneId: '' };
    const o = JSON.parse(raw) as Partial<{ bookId: string; chapterId: string; sceneId: string }>;
    return { bookId: o.bookId ?? '', chapterId: o.chapterId ?? '', sceneId: o.sceneId ?? '' };
  } catch {
    return { bookId: '', chapterId: '', sceneId: '' };
  }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const nav = useNavigate();
  const location = useLocation();

  const [books, setBooks] = useState<BookMeta[]>([]);
  const [bookId, setBookId] = useState('');
  const [chapterId, setChapterId] = useState('');
  const [sceneId, setSceneId] = useState('');
  const [tree, setTree] = useState<BookTree | null>(null);
  const bookIdRef = useRef('');
  bookIdRef.current = bookId;
  const [globalView, setGlobalView] = useState<GlobalView | null>(null);

  /* ---------- 首帧：从 localStorage 恢复 ---------- */
  useEffect(() => {
    const q = new URLSearchParams(location.search);
    const stored = readStored();
    const deep = pathScene(location.pathname);
    // 深链（/director|studio/:sceneId）不沿用记忆里的书：场景所属书由下面的反查决定，
    // 否则"先逛异能007 → 再打开雨夜书房的深链"会串书（上下文条与页面内容不一致）。
    setBookId(deep ? q.get('book') || '' : q.get('book') || stored.bookId || '');
    setChapterId(q.get('chapter') || stored.chapterId || '');
    setSceneId(deep || q.get('scene') || stored.sceneId || '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ---------- 后续：URL 变化（外部 navigate）时同步 ---------- */
  useEffect(() => {
    const q = new URLSearchParams(location.search);
    const deep = pathScene(location.pathname);
    if (deep) setSceneId(deep);
    else if (q.get('scene')) setSceneId(q.get('scene') as string);
    if (q.get('chapter')) setChapterId(q.get('chapter') as string);
    if (q.get('book')) setBookId(q.get('book') as string);
  }, [location.pathname, location.search]);

  /* ---------- 书列表 ---------- */
  const refreshBooks = useCallback(() => {
    listBooks()
      .then(setBooks)
      .catch(() => setBooks([]));
  }, []);
  useEffect(() => {
    refreshBooks();
  }, [refreshBooks]);

  // 没有书 id 且**非深链**（无场景）→ 取第一本。
  // 深链（/director/:sceneId）必须等下面的"场景 → 章 → 书"反查：否则这里会先落第一本书，
  // 反查 effect 的 cleanup 随 bookId 变化触发 cancel → 反查结果被丢弃（实测 /director/scene-betrayal-night
  // 被判成异能007，角色判据与上下文条全错）。
  useEffect(() => {
    if (bookId || sceneId || books.length === 0) return;
    setBookId(books[0].id);
  }, [bookId, sceneId, books]);

  /* ---------- 书树 + 全局视图（状态点数据源）---------- */
  const loadTree = useCallback((id: string) => {
    if (!id) {
      setTree(null);
      setGlobalView(null);
      return;
    }
    fetchBookTree(id)
      .then(setTree)
      .catch(() => setTree(null));
    fetchGlobalView(id)
      .then(setGlobalView)
      .catch(() => setGlobalView(null));
  }, []);
  useEffect(() => {
    loadTree(bookId);
  }, [bookId, loadTree]);

  const reload = useCallback(() => {
    loadTree(bookId);
    refreshBooks();
  }, [bookId, loadTree, refreshBooks]);

  /* ---------- 深链：按 场景 → 章 → 书 反查 ---------- */
  useEffect(() => {
    if (!sceneId || books.length === 0) return;
    // 场景已在本书记忆的书树里 → 无需反查
    const inTree = (tree?.chapters ?? []).some((c) => (c.scenes ?? []).some((s) => s.id === sceneId));
    if (inTree) return;
    let cancel = false;
    void (async () => {
      let resolved = '';
      try {
        const sc = await fetch(`${API_BASE}/api/v1/scenes/${sceneId}`).then((r) => (r.ok ? r.json() : null));
        const ch = sc?.chapter_id
          ? await fetch(`${API_BASE}/api/v1/chapters/${sc.chapter_id}`).then((r) => (r.ok ? r.json() : null))
          : null;
        if (ch?.book_id) resolved = ch.book_id;
        if (!cancel && ch?.id) setChapterId(ch.id);
      } catch {
        /* 后端不可用：回落到第一本 */
      }
      if (cancel) return;
      if (resolved) setBookId(resolved);
      else if (!bookIdRef.current) setBookId(books[0].id);
    })();
    return () => {
      cancel = true;
    };
  }, [sceneId, tree, books]);

  /* ---------- 派生：章 / 场景 ---------- */
  const chapters: ChapterOption[] = useMemo(
    () =>
      (tree?.chapters ?? []).map((c) => ({
        id: c.id,
        title: c.title,
        order_no: c.order_no ?? 0,
        scenes: (c.scenes ?? []).map((s) => ({
          id: s.id,
          title: s.title,
          chapter_id: s.chapter_id,
          stage_desc: s.stage_desc,
          goal: s.goal,
          characters: (s.characters ?? []).length,
        })),
      })),
    [tree],
  );
  const allScenes = useMemo(() => chapters.flatMap((c) => c.scenes), [chapters]);
  const scene = useMemo(() => allScenes.find((s) => s.id === sceneId) ?? null, [allScenes, sceneId]);
  const scenes = useMemo(
    () => (chapterId ? chapters.find((c) => c.id === chapterId)?.scenes ?? [] : allScenes),
    [chapters, chapterId, allScenes],
  );
  // 角色是**书级**资产（list_characters_for_scene 会并上书级 + 场景特设），
  // 场景内联 characters 通常为空 → 判据必须走书级接口，否则"未配角色"恒假警报。
  const [characterCount, setCharacterCount] = useState(0);
  useEffect(() => {
    if (!bookId) {
      setCharacterCount(0);
      return;
    }
    let cancel = false;
    listBookCharacters(bookId)
      .then((rows) => {
        if (!cancel) setCharacterCount(rows.length);
      })
      .catch(() => {
        if (!cancel) setCharacterCount(0);
      });
    return () => {
      cancel = true;
    };
  }, [bookId, tree]);

  // 章失效（换书/深链）→ 回落到场景所属章或第一章
  useEffect(() => {
    if (chapters.length === 0) return;
    if (chapters.some((c) => c.id === chapterId)) return;
    setChapterId(scene?.chapter_id ?? chapters[0].id);
  }, [chapters, chapterId, scene]);

  // 场景为空且有场景可选 → 自动取第一个（保证"一键直达"可用）
  useEffect(() => {
    if (sceneId || allScenes.length === 0) return;
    setSceneId(allScenes[0].id);
  }, [sceneId, allScenes]);

  /* ---------- 落点写 URL + 记忆 ---------- */
  useEffect(() => {
    if (!bookId) return;
    const q = new URLSearchParams(location.search);
    const deep = pathScene(location.pathname);
    let changed = false;
    const put = (k: string, v: string) => {
      if (q.get(k) !== v) {
        q.set(k, v);
        changed = true;
      }
    };
    put('book', bookId);
    if (chapterId) put('chapter', chapterId);
    if (sceneId && !deep) put('scene', sceneId);
    if (changed) nav({ pathname: location.pathname, search: q.toString() }, { replace: true });
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({ bookId, chapterId, sceneId }));
    } catch {
      /* 无痕模式忽略 */
    }
  }, [bookId, chapterId, sceneId, location.pathname, location.search, nav]);

  /* ---------- 状态点 ---------- */
  const status: WorkspaceStatus = useMemo(() => {
    const curve = globalView?.curve ?? [];
    return {
      outline: chapters.length > 0,
      turns: curve.some((c) => (c.turns ?? 0) > 0),
      prose: curve.some((c) => (c.prose_chars ?? 0) > 0),
      cast: characterCount > 0,
    };
  }, [chapters, globalView, characterCount]);

  /* ---------- 上一步 / 下一步 ---------- */
  const setBook = useCallback((id: string) => {
    setBookId(id);
    setChapterId('');
    setSceneId('');
  }, []);

  const stepLink = useCallback(
    (from: StepKey, dir: 'prev' | 'next'): StepLink | null => {
      const toDirector = sceneId ? `/director/${sceneId}` : '/director';
      const toStudio = sceneId ? `/studio/${sceneId}` : '/studio';
      const idx = allScenes.findIndex((s) => s.id === sceneId);
      const nextScene = idx >= 0 && idx + 1 < allScenes.length ? allScenes[idx + 1] : null;
      const table: Record<StepKey, { prev: StepLink | null; next: StepLink | null }> = {
        maestro: {
          prev: { label: '书架', to: '/' },
          next: { label: '导演台', to: toDirector },
        },
        director: {
          prev: { label: '主笔创作', to: '/maestro' },
          next: { label: '正文协作', to: toStudio },
        },
        studio: {
          prev: { label: '导演台', to: toDirector },
          next: nextScene
            ? { label: '下一场', to: `/studio/${nextScene.id}` }
            : { label: '概览复盘', to: `/dashboard?book=${bookId}` },
        },
        dashboard: {
          prev: { label: '正文协作', to: toStudio },
          next: null,
        },
        settings: {
          prev: { label: '主笔创作', to: '/maestro' },
          next: null,
        },
      };
      return table[from][dir];
    },
    [sceneId, allScenes, bookId],
  );

  /* ---------- 前置条件（可点击直达修复）---------- */
  const blocker = useCallback(
    (from: StepKey): Blocker | null => {
      if (from !== 'maestro' && from !== 'director' && from !== 'studio') return null;
      if (!sceneId) return { text: '尚未选择场景', to: '/maestro' };
      if (!status.cast) return { text: '尚未配置上场角色 · 一键生成', to: `/characters?book=${bookId}` };
      return null;
    },
    [sceneId, status.cast, bookId],
  );

  const value: WorkspaceValue = {
    books,
    bookId,
    book: books.find((b) => b.id === bookId) ?? null,
    tree,
    globalView,
    chapters,
    chapterId,
    sceneId,
    scene,
    scenes,
    characterCount,
    status,
    setBook,
    setChapter: setChapterId,
    setScene: setSceneId,
    reload,
    refreshBooks,
    stepLink,
    blocker,
  };

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceValue {
  const v = useContext(WorkspaceContext);
  if (!v) throw new Error('useWorkspace 必须在 WorkspaceProvider 内使用');
  return v;
}
