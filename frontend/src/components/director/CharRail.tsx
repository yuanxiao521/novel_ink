import { useState } from 'react';
import type { SimUIState } from '../../hooks/useDirectorSim';
import { CHANNEL_CLS, CHANNEL_TAG, type Belief } from '../../types/types';
import { CHARS, type CharSpec } from './characters';

function BeliefItem({ b }: { b: Belief }) {
  return (
    <div className="belief-item">
      <span className={`belief-tag ${CHANNEL_CLS[b.channel] || 'tag-infer'}`}>
        {CHANNEL_TAG[b.channel] || '推测'}
      </span>
      <span className="belief-text">{b.text}</span>
    </div>
  );
}

function CharCard({ spec, beliefs, ov }: { spec: CharSpec; beliefs: Belief[]; ov?: SimUIState['overrides'][string] }) {
  const moodColor = spec.moodColor;
  const [thoughtOpen, setThoughtOpen] = useState(false); // 思考区折叠/展开
  return (
    <div className="char-card" data-char-id={spec.id}>
      <div className="char-card-head">
        <img className="char-portrait" src={spec.portrait} alt={`${spec.name}立绘`} />
        <div className="char-name-col">
          <span className="char-name">{spec.name}</span>
          <span className="char-mood" style={moodColor ? { color: moodColor } : undefined}>
            {ov?.mood || spec.mood}
          </span>
        </div>
      </div>
      <div className="char-goal-row">
        <span className="char-goal">{spec.goal}</span>
        <span className="char-weight">{spec.weight}</span>
      </div>
      <div className="char-emotion">
        <div className="emotion-label-row">
          <span className="emotion-label-text">情绪水位</span>
          <span className="emotion-val">{spec.emotion}%</span>
        </div>
        <div className="emotion-track">
          <div className="emotion-fill" style={{ width: `${spec.emotion}%` }} />
        </div>
        <div className="emotion-scale">
          <span>平静</span>
          <span>爆发</span>
        </div>
      </div>
      <div className="char-belief-section">
        <div className="belief-header">
          <span className="belief-title">信念账本</span>
          <span className="belief-count">{(beliefs || spec.beliefs).length} 条</span>
        </div>
        <div className="belief-items">
          {(beliefs || spec.beliefs).map((b, i) => (
            <BeliefItem key={i} b={b} />
          ))}
        </div>
      </div>
      {ov?.perceiving && ov?.thought == null && (
        <div className="char-thought perceiving">
          <span className="thought-arrow">·感知</span>
          正在感知现场…
        </div>
      )}
      {ov?.thought != null && (
        <div
          className={`char-thought ${thoughtOpen ? 'open' : ''}`}
          onClick={() => setThoughtOpen(!thoughtOpen)}
          title={thoughtOpen ? '收起思考' : '展开思考'}
        >
          <span className="thought-arrow">·思考</span>
          <span className="thought-text">{ov.thought}</span>
          {!thoughtOpen && <span className="thought-more">展开</span>}
        </div>
      )}
      {ov?.action != null && <div className="char-action">{ov.action}</div>}
    </div>
  );
}

export function CharRail({ state }: { state: SimUIState }) {
  return (
    <aside className="char-rail">
      <div className="rail-header">
        <span className="rail-title">角色</span>
        <button className="view-switch">
          全局视角
          <svg width="8" height="8" viewBox="0 0 8 8" fill="none">
            <path d="M1.5 2.5L4 5L6.5 2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
      <div className="char-cards">
        {CHARS.map((c) => (
          <CharCard
            key={c.id}
            spec={c}
            beliefs={state.beliefs[c.id] || c.beliefs}
            ov={state.overrides[c.id]}
          />
        ))}
      </div>
    </aside>
  );
}