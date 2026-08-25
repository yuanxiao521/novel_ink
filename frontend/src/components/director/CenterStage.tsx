import { useState } from 'react';
import { charName } from './characters';
import chenImg from '../../assets/portrait-chenmo.png';
import liwImg from '../../assets/portrait-liwen.png';
import type { SimUIState } from '../../hooks/useDirectorSim';

export function CenterStage({ state }: { state: SimUIState }) {
  const [tab, setTab] = useState<'blackboard' | 'prose'>('blackboard');

  return (
    <main className="center-stage">
      <div className="stage-header">
        <div className="stage-title">
          <svg className="stage-icon" viewBox="0 0 16 16" fill="none">
            <path d="M2 12.5L6 4.5L9 10.5L11.5 6.5L14 12.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="stage-name">陈默书房 · 夜 · 雨</span>
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
    </main>
  );
}