import { useState } from 'react';
import type { SimUIState } from '../../hooks/useDirectorSim';
import { useDirectorChat } from '../../hooks/useDirectorChat';

export function DirectorPanel({
  state,
  simId,
  onAgree,
  onReject,
}: {
  state: SimUIState;
  simId: string | null;
  onAgree: () => void;
  onReject: () => void;
}) {
  const t = state.tension;
  const pct = t ? Math.max(0, Math.min(100, Math.round(t.val))) : 68;
  const trendMap: Record<string, string> = {
    up: '张力上升中',
    down: '张力回落中',
    flat: '张力趋于平稳',
  };
  const trendText = t ? trendMap[t.trend] || '张力指数' : '上升中 · 距上次冲突 3 回合';

  const raisePending = state.raise?.pending;
  const totalFuse = state.guard?.fuse ?? 0;

  // 作者↔导演 共创对话（逐 token 流式）
  const chat = useDirectorChat(simId);
  const [draft, setDraft] = useState('');

  const submitChat = () => {
    const text = draft;
    if (!text.trim()) return;
    setDraft('');
    void chat.send(text);
  };

  return (
    <aside className="director-console">
      <div className="console-header">
        <span className="console-title">导演控制台</span>
        <span className="console-lamp"></span>
      </div>

      <div className="console-panel">
        <div className="tension-head">
          <span className="tension-label">张力指数</span>
          <span className="tension-val">{state.tension ? `${pct}%` : '68%'}</span>
        </div>
        <div className="tension-track">
          <div className="tension-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="tension-trend">{trendText}</div>
      </div>

      <div className="console-panel director-panel">
        <div className="panel-title">本回合导演提示</div>
        {state.hints.length ? (
          state.hints.map((h, i) => (
            <div className={i === 1 ? 'hint gold' : 'hint'} key={`hint-${i}`}>
              {h}
            </div>
          ))
        ) : (
          <div className="hint">舞台提示：让李文先开口试探</div>
        )}
      </div>

      <div className="console-panel">
        <div className="panel-title">介入工具</div>
        <div className="intervene-btns">
          <button className="intervene-btn">注入事件</button>
          <button className="intervene-btn">信息曝光</button>
          <button className="intervene-btn">调整权重</button>
        </div>
      </div>

      <div className={`raise-card ${raisePending ? '' : 'hidden'}`}>
        <div className="raise-head">
          <span className="raise-lamp"></span>
          <span className="raise-title">导演请求介入</span>
        </div>
        <div className="raise-reason">{state.raise?.reason || '剧情走到作者决策点'}</div>
        <div className="raise-btns">
          <button className="raise-btn agree" onClick={onAgree}>
            同意
          </button>
          <button className="raise-btn reject" onClick={onReject}>
            驳回
          </button>
        </div>
      </div>

      <div className="console-panel guard-panel">
        <div className="panel-title">护栏状态</div>
        <div className="guard-rows">
          <div className="guard-row">
            <span className="guard-name">世界事实</span>
            <span className="guard-status">
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <circle cx="6" cy="6" r="6" fill="var(--accent-green)" fillOpacity="0.2" />
                <path d="M3.5 6.2L5.2 7.9L8.5 4.4" stroke="var(--accent-green)" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span className="pass">通过</span>
            </span>
          </div>
          <div className="guard-row">
            <span className="guard-name">人设一致</span>
            <span className="guard-status">
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <circle cx="6" cy="6" r="6" fill="var(--accent-green)" fillOpacity="0.2" />
                <path d="M3.5 6.2L5.2 7.9L8.5 4.4" stroke="var(--accent-green)" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span className="pass">通过</span>
            </span>
          </div>
          <div className="guard-row">
            <span className="guard-name">记忆 RAG</span>
            <span className="guard-status">
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <circle cx="6" cy="6" r="6" fill="var(--text-muted)" />
                <rect x="3" y="5.4" width="6" height="1.2" rx="0.6" fill="var(--text-secondary)" />
              </svg>
              <span className="pending">二阶段</span>
            </span>
          </div>
        </div>
        <div className="guard-foot">
          <span className="guard-foot-left">本回合拦截 {state.guard?.blocks ?? 0} 次</span>
          <span className="guard-foot-right">熔断 {totalFuse} 次</span>
        </div>
      </div>

      <div className="console-panel chat-panel">
        <div className="panel-title">与导演共创</div>
        <div className="chat-list">
          {chat.messages.length === 0 && (
            <div className="chat-empty">
              {simId ? '问导演：下一步怎么走、该曝光什么、让谁先动摇。' : '模拟未就绪，稍等…'}
            </div>
          )}
          {chat.messages.map((m) => (
            <div key={m.id} className={`chat-msg ${m.role}`}>
              <div className="chat-bubble">
                {m.error ? (
                  <span className="chat-error">{m.error}</span>
                ) : (
                  <span>
                    {m.text}
                    {m.streaming && <span className="chat-caret"></span>}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
        <div className="chat-input-row">
          <input
            className="chat-input"
            placeholder={chat.busy ? '导演思考中…（可停止）' : '对导演说…'}
            value={draft}
            disabled={chat.busy}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submitChat();
              }
            }}
          />
          {chat.busy ? (
            <button className="chat-btn stop" title="停止对话" onClick={chat.stop}>
              ■
            </button>
          ) : (
            <button
              className="chat-btn send"
              title="发送"
              onClick={submitChat}
              disabled={!draft.trim()}
            >
              →
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}