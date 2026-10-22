import { useState } from 'react';
import type { SimUIState } from '../../hooks/useDirectorSim';
import { CHANNEL_CLS, CHANNEL_TAG, type Belief } from '../../types/types';
import { CastPanel } from './CastPanel';
import { setSimCast } from '../../api/novel';
import { EmptyState } from '../common/EmptyState';
import { charInitial, charOf, isDemoChar, TIER_LABEL, type CharInfo, type CharSpec } from './characters';

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

function CharCard({ spec, info, beliefs, ov }: { spec: CharSpec; info?: CharInfo; beliefs: Belief[]; ov?: SimUIState['overrides'][string] }) {
  const moodColor = spec.moodColor;
  const [thoughtOpen, setThoughtOpen] = useState(false); // 思考区折叠/展开
  // v1.63：真实书角色没有立绘/权重/情绪基线 —— 名字与目标取接口数据，
  // 缺失字段直接不渲染（以前显示成 char-xxxx 原始 id / 破图 / 空目标 / 0%）。
  const known = isDemoChar(spec.id);
  const name = info?.name?.trim() || spec.name;
  const tierLabel = info?.tier ? TIER_LABEL[info.tier] || info.tier : '';
  const goal = info?.goal?.trim() || spec.goal;
  const mood = ov?.mood || (known ? spec.mood : '');
  return (
    <div className="char-card" data-char-id={spec.id}>
      <div className="char-card-head">
        {known ? (
          <img className="char-portrait" src={spec.portrait} alt={`${name}立绘`} />
        ) : (
          <span className="char-portrait is-initial" title={name}>{charInitial(name)}</span>
        )}
        <div className="char-name-col">
          <span className="char-name">{name}</span>
          <span className="char-mood" style={moodColor ? { color: moodColor } : undefined}>
            {mood || '待推演'}
          </span>
        </div>
        {tierLabel ? <span className="char-tier-badge">{tierLabel}</span> : null}
      </div>
      <div className="char-goal-row">
        <span className="char-goal" title={goal}>{goal || '本场目标未定'}</span>
        {known ? <span className="char-weight">{spec.weight}</span> : null}
      </div>
      {known && (
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
      )}
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
        <EmptyState
          compact
          icon="☺"
          title="暂无上场角色"
          desc="从该书角色库选择本场上场的角色。"
        >
          <button className="ws-btn ws-btn-primary" onClick={() => setDrawerOpen(true)} disabled={!cast?.simId}>
            配置上场角色
          </button>
        </EmptyState>
      );
    }
    // 有上场角色：charOf 给演示角色的基线，state.chars 给真实书的名字/档位/目标（v1.63）
    return state.charIds.map((cid) => {
      const spec = charOf(cid);
      return (
        <CharCard
          key={cid}
          spec={spec}
          info={state.chars[cid]}
          beliefs={state.beliefs[cid] || spec.beliefs}
          ov={state.overrides[cid]}
        />
      );
    });
  };

  return (
    // v1.63：aside 只做「定位容器」，滚动交给内层 .char-rail-scroll ——
    // 否则 aside 的 overflow:auto 会把比它宽的抽屉裁掉（实测左边缺 72~84px）。
    <aside className="char-rail">
      <div className="char-rail-scroll">
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
      </div>

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
                <EmptyState compact icon="☁" title="暂无世界事实" desc="推演与作者注入都会进这块黑板。" />
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
                <EmptyState compact icon="❦" title="暂无信念沉淀" desc="角色在推演里「改变想法」时会写进这里。" />
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
                <EmptyState compact icon="✎" title="暂无导演提示" />
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