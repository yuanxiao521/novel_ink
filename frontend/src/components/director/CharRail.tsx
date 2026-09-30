import { useState } from 'react';
import type { SimUIState } from '../../hooks/useDirectorSim';
import { CHANNEL_CLS, CHANNEL_TAG, type Belief } from '../../types/types';
import { CastPanel } from './CastPanel';
import { setSimCast } from '../../api/novel';
import { charOf, type CharSpec } from './characters';

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

export function CharRail({ state, cast }: { state: SimUIState; cast?: { simId: string | null; bookId: string; refresh: () => void } }) {
  const [drawerOpen, setDrawerOpen] = useState(false); // 配置上场角色（抽屉）
  const [globalOpen, setGlobalOpen] = useState(false); // 全局视角（右滑层）
  const [castSaving, setCastSaving] = useState(false);

  const hasCast = state.charIds.length > 0;

  const saveCast = async (ids: string[]) => {
    if (!cast?.simId) return;
    setCastSaving(true);
    try {
      await setSimCast(cast.simId, ids);
      setDrawerOpen(false);
      cast.refresh();
    } catch (e) {
      console.warn('[导演台] 选角失败：', e);
    } finally {
      setCastSaving(false);
    }
  };

  const renderCharArea = () => {
    if (state.viewing) return null;
    if (!hasCast) {
      return (
        <div className="char-empty" style={{ padding: 20, textAlign: 'center' }}>
          暂无上场角色
          <br />
          <button className="view-switch" style={{ marginTop: 10 }} onClick={() => setDrawerOpen(true)} disabled={!cast?.simId}>
            配置上场角色
          </button>
        </div>
      );
    }
    // 有上场角色：用 charOf 查每个 ID（静态库 + 新书角色均支持）
    return state.charIds.map((cid) => {
      const spec = charOf(cid);
      return (
        <CharCard
          key={cid}
          spec={spec}
          beliefs={state.beliefs[cid] || spec.beliefs}
          ov={state.overrides[cid]}
        />
      );
    });
  };

  return (
    <aside className="char-rail" style={{ position: 'relative' }}>
      <div className="rail-header">
        <span className="rail-title">角色</span>
        <div className="rail-actions">
          {hasCast && (
            <button className="view-switch" title="配置上场角色" onClick={() => setDrawerOpen(true)} disabled={!cast?.simId}>
              ⚙ 配置
            </button>
          )}
          <button className="view-switch" title="全局视角" onClick={() => setGlobalOpen(true)}>
            全局视角
            <svg width="8" height="8" viewBox="0 0 8 8" fill="none">
              <path d="M1.5 2.5L4 5L6.5 2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>
      </div>
      <div className="char-cards">{renderCharArea()}</div>

      {/* —— 选角抽屉（有角色后，右侧滑出） —— */}
      {drawerOpen && cast && (
        <div className="rail-overlay" onClick={() => setDrawerOpen(false)}></div>
      )}
      {drawerOpen && cast && (
        <div className="rail-drawer">
          <div className="rail-drawer-head">
            <span>配置上场角色</span>
            <button className="view-switch" onClick={() => setDrawerOpen(false)}>✕</button>
          </div>
          <CastPanel
            bookId={cast.bookId}
            currentIds={state.charIds}
            onSave={saveCast}
            onCancel={() => setDrawerOpen(false)}
            saving={castSaving}
            footerHint="cast 不重启回合：只换上场角色与信念"
          />
        </div>
      )}

      {/* —— 全局视角（右滑层 · 实时状态） —— */}
      {globalOpen && <div className="rail-overlay" onClick={() => setGlobalOpen(false)}></div>}
      {globalOpen && (
        <div className="rail-drawer global">
          <div className="rail-drawer-head">
            <span>全局视角</span>
            <button className="view-switch" onClick={() => setGlobalOpen(false)}>✕</button>
          </div>
          <div className="global-body">
            <div className="global-block">
              <div className="global-label">回合 / 张力</div>
              <div className="global-line">
                T-{state.turn ?? '—'} · 张力 {state.tension?.val ?? '—'}（{state.tension?.trend ?? 'flat'}）
              </div>
            </div>
            <div className="global-block">
              <div className="global-label">世界事实</div>
              {state.worldFacts.length === 0 && state.envRows.length === 0 ? (
                <div className="char-empty">暂无世界事实</div>
              ) : (
                <div className="global-list">
                  {state.worldFacts.map((f) => (
                    <div className="global-item" key={f.id}>· {f.text}</div>
                  ))}
                  {state.envRows.filter((r) => !state.worldFacts.some((f) => f.text === r)).map((r, i) => (
                    <div className="global-item" key={`env-${i}`}>· {r}</div>
                  ))}
                </div>
              )}
            </div>
            <div className="global-block">
              <div className="global-label">角色信念摘要</div>
              {Object.keys(state.beliefs).length === 0 ? (
                <div className="char-empty">暂无信念沉淀</div>
              ) : (
                Object.entries(state.beliefs).map(([cid, bl]) => (
                  <div className="global-char" key={cid}>
                    <span className="global-char-name">{cid.slice(0, 1).toUpperCase() + cid.slice(1)}</span>
                    <div className="global-blist">
                      {(bl || []).slice(0, 3).map((b, i) => (
                        <div className="global-item" key={i}>· {b.text}</div>
                      ))}
                    </div>
                  </div>
                ))
              )}
            </div>
            <div className="global-block">
              <div className="global-label">导演提示</div>
              {state.hints.length === 0 ? (
                <div className="char-empty">暂无导演提示</div>
              ) : (
                <div className="global-list">
                  {state.hints.map((h, i) => (
                    <div className="global-item" key={i}>· {h}</div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}