import { useState } from 'react';
import { charName } from './characters';
import chenImg from '../../assets/portrait-chenmo.png';
import liwImg from '../../assets/portrait-liwen.png';
import type { SimUIState } from '../../hooks/useDirectorSim';

const TYPE_LABEL: Record<string, string> = { conflict: '冲突', dialogue: '对话', action: '行动', info: '信息' };

export interface FinalizeProps {
  /** 点击「定稿入库」：聚合本场景成文写入 scenes.final_prose */
  onFinalize: () => void;
  finalizing: boolean;
  finalized: { wordCount: number } | null;
}

export function CenterStage({ state, playing, finalize }: { state: SimUIState; playing: boolean; finalize?: FinalizeProps }) {
  const [tab, setTab] = useState<'blackboard' | 'prose'>('blackboard');
  const viewing = state.viewing;

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
          <span className="stage-name">{viewing ? `历史回合 T-${viewing.turn} · 张力 ${viewing.tension}%` : '陈默书房 · 夜 · 雨'}</span>
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
            <div className="env-section">
          <div className="section-title blue">环境事实</div>
          <div className="env-row">
            <span className="env-dot"></span>
            <span className="env-text">窗外下着雨，雨声渐密</span>
            <span className="env-vis">陈默✓ 李文✓ 周婶✓</span>
          </div>
          <div className="env-row">
            <span className="env-dot"></span>
            <span className="env-text">保险柜门半开，锁孔有新鲜划痕</span>
            <span className="env-vis">陈默✓ 李文✓ 周婶✗</span>
          </div>
          {state.envRows.map((r, i) => (
            <div className="env-row" key={`env-${i}`}>
              <span className="env-dot"></span>
              <span className="env-text">{r}</span>
            </div>
          ))}
        </div>

        <div className="event-section">
          <div className="section-title cyan">当前回合事件</div>
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
          <div className="summary-row">
            <span className="summary-name">陈默</span>
            <span className="summary-text">
              {state.overrides.chenmo?.mood || '警觉'} —— 他注意到李文的语气不对
            </span>
          </div>
          <div className="summary-row">
            <span className="summary-name">李文</span>
            <span className="summary-text">
              {state.overrides.liwen?.mood || '紧张'} —— 她在试探保险柜的事
            </span>
          </div>
        </div>
      </div>

      <div className={`prose-view stage-pane ${tab === 'prose' ? 'active' : ''}`} data-pane="prose">
        <div className="prose-header">
          <span className="prose-chapter-label">第七章</span>
          <span className="prose-word-count">1,284 字</span>
        </div>
        <div className="prose-content">
          <h2 className="prose-title">雨夜的试探</h2>

          <p className="prose-paragraph">
            窗外的雨越下越密，敲打着窗棂，像某种不安的节拍。陈默坐在书桌后，目光落在那杯早已凉透的茶上，却没有端起来的意思。
          </p>
          <p className="prose-paragraph">
            李文站在桌前，手指无意识地绞着衣角。沉默了几秒，她终于开口——
          </p>
          <div className="prose-dialogue">
            <img className="prose-dialogue-avatar" src={liwImg} alt={charName('liwen')} />
            <div className="prose-dialogue-body">
              <div className="prose-dialogue-name">李文</div>
              <div className="prose-dialogue-text">「默哥，这文件……你什么时候放的？」</div>
            </div>
          </div>
          <p className="prose-paragraph">陈默抬起眼，没有直接回答，只是缓缓问——</p>
          <div className="prose-dialogue">
            <img className="prose-dialogue-avatar" src={chenImg} alt={charName('chenmo')} />
            <div className="prose-dialogue-body">
              <div className="prose-dialogue-name">陈默</div>
              <div className="prose-dialogue-text">「你怎么会问这个？」</div>
            </div>
          </div>
          <p className="prose-paragraph">
            李文的手指停住了。她望着陈默，那眼神里有试探，也有某种更深的、几乎要溢出来的东西。雨声忽然变得很响，像要把什么东西盖住。
          </p>

          {state.prose.map((t, i) => (
            <p className="prose-paragraph" key={`prose-${i}`}>
              {t}
            </p>
          ))}
        </div>
        <div className="prose-footer">
          <button className="prose-nav-btn">上一章</button>
          <span className="prose-progress">第 7 章 / 共 23 章</span>
          <button className="prose-nav-btn">下一章</button>
        </div>
      </div>
        </>
      )}
    </main>
  );
}