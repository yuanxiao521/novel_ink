import { useEffect, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { useTheme } from '../theme/ThemeContext';
import { useDirectorSim } from '../hooks/useDirectorSim';
import { fetchBookTree, finalizeSim, listBooks } from '../api/novel';
import type { BookMeta, BookTree } from '../api/novel';
import { API_BASE } from '../types/types';
import { CharRail } from '../components/director/CharRail';
import { CenterStage } from '../components/director/CenterStage';
import { DirectorPanel } from '../components/director/DirectorPanel';
import { TimelineBar } from '../components/director/TimelineBar';
import { ReaderView } from '../components/director/ReaderView';

function pad(n: number): string {
  return n < 10 ? '0' + n : String(n);
}

/** 推演目标：当前场景 + 来源书/章 + 是否新开。sceneId 为空 = 未选场景（空态引导） */
interface DirectorTarget {
  sceneId: string;
  bookId?: string;
  chapterId?: string;
  fresh?: boolean;
}

export function DirectorPage() {
  const { moodLabel, theme, toggleTheme, cycleMood } = useTheme();
  const { sceneId: routeSceneId } = useParams();
  const location = useLocation();
  // 深链：从主笔/概览进入时携带 book_id/chapter_id
  const navState = (location.state ?? {}) as { book_id?: string; chapter_id?: string };

  // —— 书选择（驱动顶部场景下拉；同人物页交互）——
  const [books, setBooks] = useState<BookMeta[]>([]);
  const [bookId, setBookId] = useState('');
  const [selTree, setSelTree] = useState<BookTree | null>(null);

  // —— 推演目标：仅深链 sceneId 直达；否则空态（用户从顶部下拉选场景，同角色页"选书→列表"）——
  const [target, setTarget] = useState<DirectorTarget>(() => {
    if (routeSceneId) {
      return {
        sceneId: routeSceneId,
        bookId: navState.book_id,
        chapterId: navState.chapter_id,
        fresh: new URLSearchParams(location.search).get('fresh') === '1',
      };
    }
    return { sceneId: '' };
  });
  const sceneId = target.sceneId || '';

  const { state, playing, simId, play, pause, step, viewTurn, exitView, rewindTo, agreeRaise, rejectRaise, refresh } = useDirectorSim(
    sceneId || undefined,
    target.bookId,
    target.chapterId,
    target.fresh,
  );
  const [view, setView] = useState<'workbench' | 'reader'>('workbench');
  const [finalizing, setFinalizing] = useState(false);
  const [finalized, setFinalized] = useState<{ wordCount: number } | null>(null);

  // 挂载：加载书列表；确定当前书（深链 book_id → 上次记忆 → 第一本）
  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const bs = await listBooks();
        if (cancel) return;
        setBooks(bs);
        let bid = navState.book_id;
        if (!bid && routeSceneId) {
          // 深链带上/带错书时按 场景→章→书 反查
          const sc = await fetch(`${API_BASE}/api/v1/scenes/${routeSceneId}`).then((r) => r.json());
          const ch = sc?.chapter_id ? await fetch(`${API_BASE}/api/v1/chapters/${sc.chapter_id}`).then((r) => r.json()) : null;
          bid = ch?.book_id;
        }
        if (!bid) bid = bs[0]?.id ?? '';
        if (!cancel) setBookId(bid);
      } catch {
        /* 后端不可用：留空 */
      }
    })();
    return () => { cancel = true; };
  }, [routeSceneId, navState.book_id]);

  // 当前书 → 拉书树（顶部下拉 + 内容反查共用）
  useEffect(() => {
    if (!bookId) { setSelTree(null); return; }
    let cancel = false;
    (async () => {
      try {
        const t = await fetchBookTree(bookId);
        if (!cancel) setSelTree(t);
      } catch {
        if (!cancel) setSelTree(null);
      }
    })();
    return () => { cancel = true; };
  }, [bookId]);

  const bookTitle = selTree?.title ?? books.find((b) => b.id === bookId)?.title ?? '未选场景';
  // 选角用书 id：当前书优先，回退目标书（空黑板场景无树时仍可拉角色库）
  const bookIdForCast = selTree?.id ?? target.bookId ?? bookId;

  // 当前章：目标章优先，其次按场景在书树中反推（阅读台按章读正文）
  const curChapterId =
    target.chapterId ??
    selTree?.chapters.find((c) => c.scenes.some((s) => s.id === sceneId))?.id;

  // 换书：复位目标（需重新选场景；同角色页"选书→列表"）
  const changeBook = (bid: string) => {
    setBookId(bid);
    setTarget({ sceneId: '', bookId: bid, fresh: false });
  };
  // 换场景（顶部下拉）：页内切换，不走路由 → 不整页重载
  const changeScene = (sid: string, fresh = false) => {
    const ch = selTree?.chapters.find((c) => c.scenes.some((s) => s.id === sid));
    const t: DirectorTarget = { sceneId: sid, bookId: selTree?.id ?? bookId, chapterId: ch?.id, fresh };
    setTarget(t);
  };
  // 从头新开当前场景（丢弃上次进度）
  const freshRestart = () => {
    if (!sceneId) return;
    changeScene(sceneId, true);
  };

  // 手动定稿：聚合本场景成文 → scenes.final_prose（幂等覆盖）
  useEffect(() => {
    setFinalized(null); // 换场景/换场（sim 变）后重置定稿态
  }, [simId]);

  const handleFinalize = () => {
    if (!simId || finalizing) return;
    setFinalizing(true);
    finalizeSim(simId)
      .then((r) => setFinalized({ wordCount: r.word_count }))
      .catch((e) => {
        console.warn('[导演台] 定稿失败：', e);
        setFinalized(null); // 失败复位，可重试（B10 教训：成功/失败都要复位态）
      })
      .finally(() => setFinalizing(false));
  };

  const runDotOk = state.runOk;
  const runClass = runDotOk ? 'var(--accent-green)' : 'var(--accent-gold)';
  const hasScene = selTree && selTree.chapters.flatMap((c) => c.scenes).length > 0;

  return (
    <div className="app-shell">
      <div className={`view workbench ${view === 'workbench' ? 'active' : ''}`} id="view-workbench">
        <header className="topbar">
          <div className="brand-group">
            <span className="brand-dot"></span>
            <Link className="brand-title" to="/dashboard" style={{ textDecoration: 'none' }}>
              {bookTitle}
            </Link>
            <svg className="brand-caret" viewBox="0 0 10 10" fill="none">
              <path d="M2 3.5L5 6.5L8 3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <div className="scene-group">
            <select
              className="scene-switch"
              value={bookId}
              title="切换书"
              onChange={(e) => changeBook(e.target.value)}
            >
              {books.length === 0 && <option value="">（暂无书）</option>}
              {books.map((b) => (
                <option key={b.id} value={b.id}>{b.title}</option>
              ))}
            </select>
            <select
              className="scene-switch scene-picker"
              value={sceneId}
              title="选择 章节/场景"
              onChange={(e) => changeScene(e.target.value)}
            >
              <option value="">{hasScene ? '— 选择场景 —' : '暂无场景'}</option>
              {(selTree?.chapters ?? []).map((c) => (
                <optgroup key={c.id} label={c.title}>
                  {c.scenes.map((s) => (
                    <option key={s.id} value={s.id}>{s.title}</option>
                  ))}
                </optgroup>
              ))}
            </select>
            {sceneId && (
              <button className="restart-btn" title="从头新开当前场景（丢弃上次进度）" onClick={freshRestart}>⟳ 新开</button>
            )}
            <span className="turn-divider"></span>
            <span className="turn-label">回合 {state.turn != null ? `T-${pad(state.turn)}` : '—'}</span>
          </div>
          <div className="right-group">
            <button className="mood-tag" title="切换场景 mood" onClick={cycleMood}>
              <span className="mood-dot"></span>
              <span className="mood-text">{moodLabel}</span>
            </button>
            <div className="run-tag">
              <span className="run-dot" style={{ background: runClass }}></span>
              <span className="run-text">{state.runText}</span>
            </div>
            <button className="theme-toggle" title="切换纸墨主题" onClick={toggleTheme}>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM8 3.5v9a4.5 4.5 0 0 0 0-9z" fill="currentColor" />
              </svg>
              <span className="theme-label">{theme === 'paper' ? '纸' : '墨'}</span>
            </button>
          </div>
        </header>

        <div className="workbench-body">
          {!sceneId ? (
            /* 空态：未选场景 → 引导从顶部下拉进入（不再独立选择页/路由跳转） */
            <div className="director-empty">
              <div className="empty-card">
                <div className="empty-title">导演台 · 未选场景</div>
                <p className="empty-sub">
                  {!selTree
                    ? '还没有书。先去「主笔」建书、再建章节与场景，或去「概览」新建。'
                    : !hasScene
                      ? '这本书还没有场景，先去「主笔」给章节添加场景。'
                      : '从上方「书 → 章节/场景」下拉选择一幕，即可开始或继续推演。'}
                </p>
                {(!selTree || !hasScene) && (
                  <Link to="/maestro">
                    <button className="empty-btn">去主笔建结构</button>
                  </Link>
                )}
              </div>
            </div>
          ) : (
            <>
              <CharRail state={state} cast={{ simId, bookId: bookIdForCast, refresh }} />
              <CenterStage
                state={state}
                playing={playing}
                cast={{ simId, bookId: bookIdForCast, refresh }}
                finalize={{ onFinalize: handleFinalize, finalizing, finalized }}
              />
              <DirectorPanel state={state} simId={simId} onAgree={agreeRaise} onReject={rejectRaise} />
            </>
          )}
        </div>

        {sceneId && (
          <TimelineBar state={state} playing={playing} onPlay={play} onPause={pause} onStep={step} onViewTurn={viewTurn} onRewind={rewindTo} onExitView={exitView} />
        )}
      </div>

      {view === 'reader' && sceneId && (
        <ReaderView
          bookTitle={bookTitle}
          bookId={selTree?.id ?? bookId}
          chapterId={curChapterId}
          chapters={(selTree?.chapters ?? []).map((c) => ({ id: c.id, title: c.title, order_no: c.order_no }))}
        />
      )}

      {sceneId && (
        <div className="view-switcher">
          <button className={view === 'workbench' ? 'active' : ''} onClick={() => setView('workbench')}>
            作者工作台
          </button>
          {view === 'reader' ? (
            <button onClick={() => setView('workbench')}>返回工作台</button>
          ) : (
            <button className="active" onClick={() => setView('reader')}>
              读者阅读台
            </button>
          )}
        </div>
      )}
    </div>
  );
}