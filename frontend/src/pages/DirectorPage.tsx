import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useTheme } from '../theme/ThemeContext';
import { useDirectorSim } from '../hooks/useDirectorSim';
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
  const { state, agreeRaise, rejectRaise } = useDirectorSim();
  const [view, setView] = useState<'workbench' | 'reader'>('workbench');

  const runDotOk = state.runOk;
  const runClass = runDotOk ? 'var(--accent-green)' : 'var(--accent-gold)';

  return (
    <div className="app-shell">
      <div className={`view workbench ${view === 'workbench' ? 'active' : ''}`} id="view-workbench">
        <header className="topbar">
          <div className="brand-group">
            <span className="brand-dot"></span>
            <Link className="brand-title" to="/dashboard" style={{ textDecoration: 'none' }}>
              背叛之夜
            </Link>
            <svg className="brand-caret" viewBox="0 0 10 10" fill="none">
              <path d="M2 3.5L5 6.5L8 3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <div className="scene-group">
            <span className="scene-label">场景：书房夜谈</span>
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
          <CenterStage state={state} />
          <DirectorPanel state={state} onAgree={agreeRaise} onReject={rejectRaise} />
        </div>

        <TimelineBar state={state} />
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