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
  // 用户是否在「书」下拉里**显式**换过书：显式选择优先，场景反查不得把书拽回去
  const userPickedBookRef = useRef(false);
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

  // 我们自己写过的地址：URL → 状态 的同步必须忽略它，否则「换章时退出深链」这类自带 nav 的操作
  // 会被 URL 里还没更新的旧 chapter 立刻拽回去（实测：章怎么点都弹回第 1 章）。
  const selfUrlRef = useRef('');
  const goTo = useCallback((pathname: string, search = '') => {
    const qs = search && search !== '?' ? (search.startsWith('?') ? search : '?' + search) : '';
    selfUrlRef.current = pathname + qs;
    nav({ pathname, search: qs }, { replace: true });
  }, [nav]);

  /* ---------- 后续：URL 变化（外部 navigate / 前进后退）时同步 ---------- */
  useEffect(() => {
    if (location.pathname + location.search === selfUrlRef.current) return;
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

  // 记忆里的书已被删除（例如清理掉的两本 ???? 空壳书）→ 回落到第一本，
  // 否则上下文条的书不在选项里、页面停在「未选场景」这种半死状态。
  useEffect(() => {
    if (!bookId || books.length === 0) return;
    if (books.some((b) => b.id === bookId)) return;
    setChapterId('');
    setSceneId('');
    setBookId(books[0].id);
  }, [bookId, books]);

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
    // 书树还没换到当前书（正在加载 / 还是上一本）→ 等下一轮，别拿旧树的场景乱猜
    if (bookIdRef.current && tree?.id !== bookIdRef.current) return;
    // 书是用户显式选的 → 场景不得反噬书：该场景不属于当前书，丢弃它并退出深链
    if (userPickedBookRef.current && bookIdRef.current) {
      setSceneId('');
      const seg = window.location.pathname.match(/^\/(director|studio)(?:\/|$)/)?.[1];
      if (seg) goTo('/' + seg);
      return;
    }
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
  }, [sceneId, tree, books, location.pathname, goTo]);

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
  // 同样要等书树换到当前书：否则换书瞬间会把**上一本的章**写进 URL/记忆（实测 chapter-01）
  useEffect(() => {
    if (chapters.length === 0) return;
    if (!bookId || tree?.id !== bookId) return;
    if (chapters.some((c) => c.id === chapterId)) return;
    setChapterId(scene?.chapter_id ?? chapters[0].id);
  }, [chapters, chapterId, scene, bookId, tree]);

  // 场景为空且有场景可选 → 自动取第一个（保证"一键直达"可用）
  // 两条约束：① 必须等书树换到当前书（否则会把上一本的场景塞进来，随后反查又判回旧书 = 「书选不了」）；
  //           ② 只在**当前章**里挑（跨章挑会出现「栏里第 2 章、场景却是第 1 章」的下拉错位）。
  useEffect(() => {
    if (sceneId) return;
    if (!bookId || tree?.id !== bookId) return;
    const wantChapter = chapterId || chapters[0]?.id || '';
    const first = chapters.find((c) => c.id === wantChapter)?.scenes[0]?.id;
    if (first) setSceneId(first);
  }, [sceneId, chapters, chapterId, bookId, tree]);

  /* ---------- 落点写 URL + 记忆 ---------- */
  useEffect(() => {
    if (!bookId) return;
    // 必须用**实时**地址：React Router 的 location 提交可能晚于同一次 effect flush 里的其它 state 更新，
    // 用 render 期的 location 会出现「子组件(SceneEntry)刚跳到 /director/:sceneId，父级又把 URL 写回 /director?scene=…」，
    // 结果停在 SceneEntry 空态页 = 用户看到的「选了卡住 / 不知道在哪本书」。
    const live = window.location;
    const q = new URLSearchParams(live.search);
    const deep = pathScene(live.pathname);
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
    if (changed) goTo(live.pathname, q.toString());
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({ bookId, chapterId, sceneId }));
    } catch {
      /* 无痕模式忽略 */
    }
  }, [bookId, chapterId, sceneId, location.pathname, location.search, goTo]);

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
  const setBook = useCallback(
    (id: string) => {
      userPickedBookRef.current = true;
      setBookId(id);
      setChapterId('');
      setSceneId('');
      // 深链路径 /director|studio/:sceneId 里的旧场景会把书拽回去（反查 → 旧书）：
      // 换书时一并退回「无场景入口」，再由上面的「自动取第一个场景」落到新书的场景。
      const live = window.location;
      const q = new URLSearchParams(live.search);
      q.delete('scene');
      q.delete('chapter');
      q.set('book', id);
      const seg = live.pathname.match(/^\/(director|studio)(?:\/|$)/)?.[1];
      goTo(seg ? '/' + seg : live.pathname, q.toString());
    },
    [goTo],
  );

  // 换场景：深链页把场景写回路径（清空则退出深链），刷新 / 返回才不会跳回旧场景
  const setScene = useCallback(
    (id: string) => {
      setSceneId(id);
      const live = window.location;
      const seg = live.pathname.match(/^\/(director|studio)(?:\/|$)/)?.[1];
      if (!seg || pathScene(live.pathname) === id) return;
      const q = new URLSearchParams(live.search);
      if (id) q.delete('scene');
      goTo(id ? '/' + seg + '/' + encodeURIComponent(id) : '/' + seg, q.toString());
    },
    [goTo],
  );

  // 换章：不属于本章的旧场景要清掉（并退出深链），否则会「栏里第 2 章、路径还是第 1 章的场景」
  const setChapter = useCallback(
    (id: string) => {
      setChapterId(id);
      const ch = tree?.chapters.find((c) => c.id === id);
      if (ch && sceneId && !(ch.scenes ?? []).some((s) => s.id === sceneId)) setScene('');
    },
    [tree, sceneId, setScene],
  );

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
    setChapter,
    setScene,
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
