import { useEffect, useRef, useState } from 'react';
import { ScriptView } from '../components/director/ScriptView';
import { ContextBar } from '../components/common/ContextBar';
import { useWorkspace } from '../context/WorkspaceContext';
import { Link, useLocation, useParams } from 'react-router-dom';
import { useTheme } from '../theme/ThemeContext';
import { useDirectorSim } from '../hooks/useDirectorSim';
import { finalizeSim } from '../api/novel';
import { CharRail } from '../components/director/CharRail';
import { CenterStage } from '../components/director/CenterStage';
import { DirectorPanel } from '../components/director/DirectorPanel';
import { TimelineBar } from '../components/director/TimelineBar';
import { ReaderView } from '../components/director/ReaderView';

function pad(n: number): string {
  return n < 10 ? '0' + n : String(n);
}

export function DirectorPage() {
  const { moodLabel, theme, toggleTheme, cycleMood } = useTheme();
  const { sceneId: routeSceneId } = useParams();
  const location = useLocation();
  // 深链：从书架/概览进入时携带 chapter_id（book_id 交给全局上下文按场景反查）
  const navState = (location.state ?? {}) as { book_id?: string; chapter_id?: string };
  // —— 书 / 场景的唯一真相源 = 全局上下文（WorkspaceContext）——
  // 本页原先自持 bookId/selTree，与上下文条各算一套，出现过「栏里雨夜书房、页内异能007」的串书；
  // 现在书与场景都从上下文读、经上下文改，页面与上下文条结构上不可能不一致。
  const {
    books, bookId, tree: selTree, sceneId: wsSceneId,
    setScene: setWsScene,
  } = useWorkspace();

  const sceneId = routeSceneId || wsSceneId || '';
  // 深链参数回推给上下文：路径 / 上下文条 / 页内三者同源。
  // 依赖只留 routeSceneId：若跟着 wsSceneId 一起跑，换书后 ws 刚自动选中的新场景
  // 会被这条旧路径参数拽回去（表现为「换书后自己跳回上一本」）。
  useEffect(() => {
    if (routeSceneId && routeSceneId !== wsSceneId) setWsScene(routeSceneId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeSceneId]);
  // 「从头新开」标志：换场景即复位（与旧 target.fresh 行为一致）
  const [fresh, setFresh] = useState(() => new URLSearchParams(location.search).get('fresh') === '1');
  // 换场景复位「新开」标志；首帧不动（否则 ?fresh=1 一挂载就被清掉）
  const firstSceneRef = useRef(true);
  useEffect(() => {
    if (firstSceneRef.current) { firstSceneRef.current = false; return; }
    setFresh(false);
  }, [sceneId]);

  // 当前章：书树里按场景反推（阅读台按章读正文）；树未到时用深链带来的章
  const curChapterId =
    selTree?.chapters.find((c) => c.scenes.some((s) => s.id === sceneId))?.id ??
    (routeSceneId === sceneId ? navState.chapter_id : undefined);
  const bookTitle = selTree?.title ?? books.find((b) => b.id === bookId)?.title ?? '未选场景';
  // 选角用书 id：书树未到（空黑板场景）时回落到上下文里的书
  const bookIdForCast = selTree?.id ?? bookId;

  const { state, playing, simId, play, pause, step, viewTurn, exitView, rewindTo, agreeRaise, rejectRaise, refresh } = useDirectorSim(
    sceneId || undefined,
    bookId || undefined,
    curChapterId,
    fresh,
  );
  const [view, setView] = useState<'workbench' | 'reader'>('workbench');
  const [scriptOpen, setScriptOpen] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [finalized, setFinalized] = useState<{ wordCount: number } | null>(null);

  // 换场景：统一走全局上下文（深链页由它把场景写回路径），本页只跟随
  const changeScene = (sid: string) => setWsScene(sid);
  // 从头新开当前场景（丢弃上次进度）
  const freshRestart = () => {
    if (!sceneId) return;
    setFresh(true);
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
        <ContextBar step="director" />
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
          <button onClick={() => setScriptOpen(true)} title="把推演回合当剧本看（含角色思考）">
            剧本台
          </button>
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

      {scriptOpen && sceneId && (
        <ScriptView sceneId={sceneId} onClose={() => setScriptOpen(false)} />
      )}
    </div>
  );
}