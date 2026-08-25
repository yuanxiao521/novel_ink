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

export function TimelineBar({ state }: { state: SimUIState }) {
  // 动画/静态占位
  const playButtons = (
    <>
      <button className="tl-btn" title="跳到开头" style={{ pointerEvents: 'none' }}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M11 10.5L7 7L3 10.5V3.5h8V10.5z" fill="currentColor" />
        </svg>
      </button>
      <button className="tl-btn" title="上一回合" style={{ pointerEvents: 'none' }}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M9.5 3.5L5.5 7L9.5 10.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      <button className="tl-btn play" title="播放/暂停" style={{ pointerEvents: 'none' }}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M4 3L4 11L11 7L4 3Z" fill="currentColor" />
        </svg>
      </button>
      <button className="tl-btn" title="下一回合" style={{ pointerEvents: 'none' }}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M4.5 3.5L8.5 7L4.5 10.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      <button className="tl-btn" title="跳到末尾" style={{ pointerEvents: 'none' }}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M3 10.5L7 7L11 10.5V3.5H3V10.5z" fill="currentColor" />
        </svg>
      </button>
    </>
  );

  const showStatic = state.finishedTurns.length === 0 && state.current === null;

  return (
    <footer className="timeline">
      <div className="tl-top">
        <div className="tl-controls">
          {playButtons}
          <div className="live-tag">
            <span className="live-dot"></span>
            <span className="live-text">实时模式</span>
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
              {state.finishedTurns.map((t) => (
                <div className={`turn-node ${t.cls}`} key={`turn-${t.turn}`}>
                  <span className="turn-dot"></span>
                  <span className="turn-label">T-{pad(t.turn)}</span>
                  <span className="turn-desc">{t.summary}</span>
                </div>
              ))}
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