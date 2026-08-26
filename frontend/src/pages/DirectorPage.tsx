import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useTheme } from '../theme/ThemeContext';
import { useDirectorSim } from '../hooks/useDirectorSim';
import { fetchBookTree } from '../api/novel';
import type { BookTree } from '../api/novel';
import { API_BASE } from '../types/types';
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
  const { sceneId } = useParams();
  const location = useLocation();
  const nav = useNavigate();
  // 路由 state 携带 book_id/chapter_id（从规划页/书架进入时传入）
  const navState = (location.state ?? {}) as { book_id?: string; chapter_id?: string };
  const { state, playing, simId, play, pause, step, viewTurn, exitView, rewindTo, agreeRaise, rejectRaise } = useDirectorSim(
    sceneId,
    navState.book_id,
    navState.chapter_id,
  );
  const [view, setView] = useState<'workbench' | 'reader'>('workbench');
  const [tree, setTree] = useState<BookTree | null>(null);

  // 顶部接真：反查书树（优先路由 state 的 book_id；没有则按 scene → chapter → book 反查）
  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        let bid = navState.book_id;
        if (!bid && sceneId) {
          const sc = await fetch(`${API_BASE}/api/v1/scenes/${sceneId}`).then((r) => r.json());
          if (sc && sc.chapter_id) {
            const ch = await fetch(`${API_BASE}/api/v1/chapters/${sc.chapter_id}`).then((r) => r.json());
            bid = ch && ch.book_id;
          }
        }
        if (!bid) return;
        const t = await fetchBookTree(bid);
        if (!cancel) setTree(t);
      } catch {
        /* 静态兜底，保留默认标题 */
      }
    })();
    return () => { cancel = true; };
  }, [sceneId, navState.book_id]);

  const curScene = tree?.chapters.flatMap((c) => c.scenes).find((s) => s.id === sceneId);
  const bookTitle = tree?.title ?? '背叛之夜';

  const runDotOk = state.runOk;
  const runClass = runDotOk ? 'var(--accent-green)' : 'var(--accent-gold)';

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
            {tree ? (
              <select
                className="scene-switch"
                value={sceneId}
                title="切换章节/场景"
                onChange={(e) => nav(`/director/${e.target.value}`)}
              >
                {tree.chapters.map((c) => (
                  <optgroup key={c.id} label={c.title}>
                    {c.scenes.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.title}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            ) : (
              <span className="scene-label">场景：{curScene?.title ?? (sceneId ? '加载中…' : '书房夜谈')}</span>
            )}
            <span className="turn-divider"></span>
            <span className="turn-label">回合 T-{state.turn ? pad(state.turn) : '07'}</span>
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
          <CharRail state={state} />
          <CenterStage state={state} playing={playing} />
          <DirectorPanel state={state} simId={simId} onAgree={agreeRaise} onReject={rejectRaise} />
        </div>

        <TimelineBar state={state} playing={playing} onPlay={play} onPause={pause} onStep={step} onViewTurn={viewTurn} onRewind={rewindTo} onExitView={exitView} />
      </div>

      {view === 'reader' && <ReaderView />}

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
    </div>
  );
}