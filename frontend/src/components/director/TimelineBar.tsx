import type { SimUIState } from '../../hooks/useDirectorSim';

const STATIC_TURNS = [
  { turn: 1, cls: 'type-action', desc: '雨夜归家' },
  { turn: 2, cls: 'type-dialogue', desc: '书房初遇' },
  { turn: 3, cls: 'type-info', desc: '周婶送茶' },
  { turn: 4, cls: 'type-conflict', desc: '保险柜疑云' },
  { turn: 5, cls: 'type-dialogue', desc: '沉默对峙' },
  { turn: 6, cls: 'type-action', desc: '文件异动' },
  { turn: 7, cls: 'type-dialogue', desc: '李文试探' },
];

function pad(n: number): string {
  return n < 10 ? '0' + n : String(n);
}

export function TimelineBar({
  state,
  playing,
  onPlay,
  onPause,
  onStep,
  onViewTurn,
  onRewind,
  onExitView,
}: {
  state: SimUIState;
  playing: boolean;
  onPlay: () => void;
  onPause: () => void;
  onStep: () => void;
  onViewTurn: (turn: number) => void;
  onRewind: (turn: number) => void;
  onExitView: () => void;
}) {
  // 点击节点 → 查看该回合；再次点击 → 退出查看
  const handleNodeClick = (turn: number) => {
    if (state.viewing?.turn === turn) onExitView();
    else onViewTurn(turn);
  };

  // 回退目标：优先当前"正在查看"的回合，否则回退到最后一个归档回合
  const replayTarget = state.viewing?.turn ?? state.archives[state.archives.length - 1]?.turn;
  const handleReplay = () => {
    if (replayTarget == null) return;
    onRewind(replayTarget);
  };

  const playButtons = (
    <>
      {playing ? (
        <button className="tl-btn play" title="暂停" onClick={onPause}>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <rect x="3" y="3" width="3" height="8" rx="0.5" fill="currentColor" />
            <rect x="8" y="3" width="3" height="8" rx="0.5" fill="currentColor" />
          </svg>
        </button>
      ) : (
        <button className="tl-btn play" title="播放" onClick={onPlay}>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M4 3L4 11L11 7L4 3Z" fill="currentColor" />
          </svg>
        </button>
      )}
      <button className="tl-btn" title="单步推进一回合" onClick={onStep}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M4.5 3.5L8.5 7L4.5 10.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          <line x1="10" y1="3.5" x2="10" y2="10.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
      <button
        className="tl-btn replay"
        title={state.viewing ? `重放到 T-${pad(state.viewing.turn)} 重演` : '重放到最近回合'}
        onClick={handleReplay}
        disabled={replayTarget == null}
      >
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M11.5 7a4.5 4.5 0 1 1-1.32-3.18" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M10.6 1.8v2.2h-2.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
    </>
  );

  const showStatic = state.finishedTurns.length === 0 && state.current === null;
  const viewing = state.viewing;

  return (
    <footer className="timeline">
      <div className="tl-top">
        <div className="tl-controls">
          {playButtons}
          <div className="live-tag">
            <span className="live-dot"></span>
            <span className="live-text">{viewing ? `查看 T-${pad(viewing.turn)}（历史）` : '实时模式'}</span>
          </div>
        </div>
        <div className="tl-meta">
          <span>速度 1x</span>
          <span>共 {state.turn ?? 7} 回合 / {state.eventCards.length || 23} 事件</span>
        </div>
        <div className="tl-legend">
          <span className="legend-item">
            <span className="legend-dot type-action"></span>行动
          </span>
          <span className="legend-item">
            <span className="legend-dot type-dialogue"></span>对话
          </span>
          <span className="legend-item">
            <span className="legend-dot type-conflict"></span>冲突
          </span>
          <span className="legend-item">
            <span className="legend-dot type-info"></span>信息
          </span>
        </div>
      </div>
      <div className="tl-track">
        {showStatic
          ? STATIC_TURNS.map((t) => (
              <div className={`turn-node ${t.cls} ${t.turn === 7 ? 'current' : ''}`} key={`static-${t.turn}`}>
                <span className="turn-dot"></span>
                <span className="turn-label">T-{pad(t.turn)}</span>
                <span className="turn-desc">{t.desc}</span>
              </div>
            ))
          : (
            <>
              {state.archives.map((arc) => {
                const isViewing = viewing?.turn === arc.turn;
                return (
                  <div
                    className={`turn-node ${arc.cls} ${isViewing ? 'viewing' : ''}`}
                    key={`arc-${arc.turn}`}
                    title={`点击查看 T-${pad(arc.turn)} · ${arc.summary}`}
                    onClick={() => handleNodeClick(arc.turn)}
                  >
                    <span className="turn-dot"></span>
                    <span className="turn-label">T-{pad(arc.turn)}</span>
                    <span className="turn-desc">{arc.summary}</span>
                  </div>
                );
              })}
              {state.current && (
                <div className="turn-node current">
                  <span className="turn-dot"></span>
                  <span className="turn-label">T-{pad(state.current.turn)}</span>
                  <span className="turn-desc">{state.current.summary}</span>
                </div>
              )}
            </>
          )}
      </div>
    </footer>
  );
}