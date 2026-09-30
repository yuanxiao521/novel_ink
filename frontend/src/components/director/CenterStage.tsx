import { useState } from 'react';
import { charName } from './characters';
import { CastPanel } from './CastPanel';
import { setSimCast } from '../../api/novel';
import type { SimUIState } from '../../hooks/useDirectorSim';

const TYPE_LABEL: Record<string, string> = { conflict: '冲突', dialogue: '对话', action: '行动', info: '信息' };

export interface FinalizeProps {
  /** 点击「定稿入库」：聚合本场景成文写入 scenes.final_prose */
  onFinalize: () => void;
  finalizing: boolean;
  finalized: { wordCount: number } | null;
}

export interface CastProps {
  simId: string | null;
  bookId: string;
  refresh: () => void;
}

export function CenterStage({
  state,
  playing,
  cast,
  finalize,
}: {
  state: SimUIState;
  playing: boolean;
  cast?: CastProps;
  finalize?: FinalizeProps;
}) {
  const [tab, setTab] = useState<'blackboard' | 'prose'>('blackboard');
  const [castOpen, setCastOpen] = useState(false);
  const [castSaving, setCastSaving] = useState(false);
  const viewing = state.viewing;

  const isEmptyBoard = state.emptyWorld && state.charIds.length === 0;

  const saveCast = async (ids: string[]) => {
    if (!cast?.simId) return;
    setCastSaving(true);
    try {
      await setSimCast(cast.simId, ids);
      setCastOpen(false);
      cast.refresh();
    } catch (e) {
      console.warn('[导演台] 选角失败：', e);
    } finally {
      setCastSaving(false);
    }
  };

  // 推演中：根据当前事件判断阶段
  const runningStatus = (() => {
    if (!playing) return null;
    // 有角色正在感知
    const perceiving = Object.entries(state.overrides).find(([, o]) => o.perceiving);
    if (perceiving) return { label: `${charName(perceiving[0])} 正在感知环境…`, phase: 'perceive' };
    // 有角色正在思考
    const thinking = Object.entries(state.overrides).find(([, o]) => o.thought && !o.action);
    if (thinking) return { label: `${charName(thinking[0])} 正在思考…`, phase: 'think' };
    // 有成文在追加
    if (state.prose.length > 0 && state.current) return { label: '正在生成正文…', phase: 'prose' };
    // 导演调度中
    if (state.hints.length > 0) return { label: '导演调度中…', phase: 'director' };
    // 默认：回合进行中
    if (state.current) return { label: `回合 T-${String(state.current.turn).padStart(2, '0')} 推演中…`, phase: 'turn' };
    return null;
  })();

  return (
    <main className="center-stage">
      <div className="stage-header">
        <div className="stage-title">
          <svg className="stage-icon" viewBox="0 0 16 16" fill="none">
            <path d="M2 12.5L6 4.5L9 10.5L11.5 6.5L14 12.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="stage-name">{viewing ? `历史回合 T-${viewing.turn} · 张力 ${viewing.tension}%` : '世界黑板'}</span>
        </div>
        <div className="tab-switch">
          <button className={`tab ${tab === 'blackboard' ? 'active' : ''}`} data-stage-tab="blackboard" onClick={() => setTab('blackboard')}>
            世界黑板
          </button>
          <button className={`tab ${tab === 'prose' ? 'active' : ''}`} data-stage-tab="prose" onClick={() => setTab('prose')}>
            成文
          </button>
        </div>
      </div>

      {/* 场景收束长程汇报（S3） */}
      {state.closeReport && !viewing && (
        <div className="close-report">
          <div className="close-report-head">📋 导演长程汇报 · 本场收束</div>
          <div className="close-report-body">
            {state.closeReport.scene_summary && (
              <div className="close-report-summary">{state.closeReport.scene_summary}</div>
            )}
            {(state.closeReport.foreshadow_updates?.length ?? 0) > 0 && (
              <div className="close-report-tags">
                伏笔推进 {(state.closeReport.foreshadow_updates ?? []).filter((f) => f.status === 'in_progress').length} 条 · 回收 {(state.closeReport.foreshadow_updates ?? []).filter((f) => f.status === 'closed').length} 条
              </div>
            )}
            {state.closeReport.next_scene_hint && (
              <div className="close-report-hint">下一场提示：{state.closeReport.next_scene_hint}</div>
            )}
            {finalize && (
              <div className="close-report-actions">
                <button
                  className="close-report-finalize-btn"
                  onClick={finalize.onFinalize}
                  disabled={finalize.finalizing}
                  title="把本场景成文定稿写入书稿（可重复定稿覆盖）"
                >
                  {finalize.finalizing
                    ? '定稿中…'
                    : finalize.finalized
                      ? `✓ 已定稿 · ${finalize.finalized.wordCount} 字 · 点击重新定稿`
                      : '✒ 定稿入库'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 推演中状态指示器 */}
      {runningStatus && !viewing && (
        <div className="running-status">
          <span className="running-spinner"></span>
          <span className="running-text">{runningStatus.label}</span>
        </div>
      )}

      {viewing ? (
        /* ---- 历史回合查看模式：展示该回的成文 + 事件 ---- */
        <div className="history-view">
          <div className="history-banner">
            <span>历史回合 T-{viewing.turn}</span>
            <span className="history-summary">{viewing.summary}</span>
          </div>
          <div className="history-pane">
            {viewing.prose && <div className="history-prose">{viewing.prose}</div>}
            {viewing.events.length > 0 ? (
              <div className="history-events">
                {viewing.events.map((ev, i) => (
                  <div className={`event-card type-${ev.kind}`} key={`h-${i}`}>
                    <div className="event-meta">
                      <span className="event-type-tag">
                        <span className={`event-type type-${ev.kind}`}>{TYPE_LABEL[ev.kind] || '信息'}</span>
                      </span>
                      <span className="event-id">{charName(ev.actor)}</span>
                    </div>
                    <div className="event-quote">{ev.text}</div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="history-empty">这一回合没有记录角色行动。</div>
            )}
          </div>
        </div>
      ) : (
        <>
          <div className={`blackboard stage-pane ${tab === 'blackboard' ? 'active' : ''}`} data-pane="blackboard">
            {isEmptyBoard && cast && cast.bookId ? (
              /* —— 空台引导卡：无角色无事实，内联展开选角 —— */
              <div className="empty-board">
                {castOpen ? (
                  <CastPanel
                    bookId={cast.bookId}
                    currentIds={[]}
                    onSave={saveCast}
                    onCancel={() => setCastOpen(false)}
                    saving={castSaving}
                    footerHint="空台开场：先配上场角色，再点「播放」"
                  />
                ) : (
                  <button className="empty-board-cta" onClick={() => setCastOpen(true)}>
                    <span className="ebb-ico">🎭</span>
                    <span className="ebb-title">本场还没有角色</span>
                    <span className="ebb-sub">从该书角色库选择本场上场的角色（配置上场角色）</span>
                  </button>
                )}
              </div>
            ) : (
              <>
            <div className="env-section">
          <div className="section-title blue">环境事实</div>
          {state.envRows.length === 0 && !state.emptyWorld && (
            <div className="env-empty">暂无环境事实。点「播放」推演后，环境事实会随回合自动生成。</div>
          )}
          {state.envRows.map((r, i) => (
            <div className="env-row" key={`env-${i}`}>
              <span className="env-dot"></span>
              <span className="env-text">{r}</span>
            </div>
          ))}
        </div>

        <div className="event-section">
          <div className="section-title cyan">当前回合事件</div>
          {state.eventCards.length === 0 && (
            <div className="event-empty">暂无事件。点底部「播放」开始推演，事件会实时出现。</div>
          )}
          {state.eventCards.map((c, i) => (
            <div className={`event-card ${c.cls}`} key={`ev-${i}`}>
              <div className="event-meta">
                <span className="event-type-tag">
                  <span className={`event-type ${c.cls}`}>{c.label}</span>
                </span>
                <span className="event-id">{c.id}</span>
              </div>
              <div className="event-quote">{c.quote}</div>
              <div className="event-source">{c.source}</div>
            </div>
          ))}
        </div>

        <div className="char-summary">
          <div className="section-title muted">角色状态摘要</div>
          {Object.keys(state.overrides).length === 0 ? (
            <div className="summary-empty">暂无角色状态。推演开始后，角色的情绪与思考会实时显示在这里。</div>
          ) : (
            Object.entries(state.overrides).map(([cid, ov]) => (
              <div className="summary-row" key={cid}>
                <span className="summary-name">{charName(cid)}</span>
                <span className="summary-text">
                  {ov.mood || '—'}{ov.thought ? ` —— ${ov.thought}` : ''}{ov.action ? ` · ${ov.action}` : ''}
                </span>
              </div>
            ))
          )}
        </div>
              </>
            )}
      </div>

      <div className={`prose-view stage-pane ${tab === 'prose' ? 'active' : ''}`} data-pane="prose">
        {state.prose.length === 0 ? (
          <div className="prose-empty">
            <div className="prose-empty-title">暂无成文</div>
            <p className="prose-empty-sub">推演开始后，正文会逐段生成在这里。<br />当前回合结束后会自动聚合为成文段落。</p>
          </div>
        ) : (
          <>
            <div className="prose-content">
              {state.prose.map((t, i) => (
                <p className="prose-paragraph" key={`prose-${i}`}>
                  {t}
                </p>
              ))}
            </div>
            {finalize && (
              <div className="prose-footer">
                <button
                  className="close-report-finalize-btn"
                  onClick={finalize.onFinalize}
                  disabled={finalize.finalizing}
                >
                  {finalize.finalizing
                    ? '定稿中…'
                    : finalize.finalized
                      ? `✓ 已定稿 · ${finalize.finalized.wordCount} 字 · 点击重新定稿`
                      : '✒ 定稿入库'}
                </button>
              </div>
            )}
          </>
        )}
      </div>
        </>
      )}
    </main>
  );
}