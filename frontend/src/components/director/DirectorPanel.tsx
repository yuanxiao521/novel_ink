import { useState } from 'react';
import type { SimUIState } from '../../hooks/useDirectorSim';
import { useDirectorChat } from '../../hooks/useDirectorChat';
import { fetchInjectPalette, interveneSim } from '../../api/novel';
import type { InjectPalette } from '../../api/novel';

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

  // 介入三件套（v1.3）：注入事件 / 信息曝光 / 目标权重调整
  type ToolPanel = null | 'event' | 'expose' | 'weight';
  const [tool, setTool] = useState<ToolPanel>(null);
  const [palette, setPalette] = useState<InjectPalette | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [eventText, setEventText] = useState('');
  const [expFact, setExpFact] = useState('');
  const [expChar, setExpChar] = useState('');
  const [expChannel, setExpChannel] = useState<'perceived' | 'told' | 'inferred'>('perceived');
  const [adjChar, setAdjChar] = useState('');
  const [adjGoal, setAdjGoal] = useState('');
  const [adjDelta, setAdjDelta] = useState(0.2);
  const [adjReason, setAdjReason] = useState('');

  const openTool = (t: ToolPanel) => {
    if (!simId) return;
    setTool(t === tool ? null : t);
    setErr(null);
    setOkMsg(null);
    if (!palette) {
      fetchInjectPalette(simId)
        .then(setPalette)
        .catch((e) => setErr(`介入数据源加载失败：${e instanceof Error ? e.message : String(e)}`));
    }
  };

  const doIntervene = (
    action: 'inject_event' | 'expose' | 'adjust_weight',
    payload: Record<string, unknown>,
    doneLabel: string,
  ) => {
    if (!simId) return;
    setBusy(true);
    setErr(null);
    setOkMsg(null);
    interveneSim(simId, action, payload)
      .then(() => {
        setOkMsg(doneLabel);
        setTool(null);
      })
      .catch((e) => setErr(`介入失败：${e instanceof Error ? e.message : String(e)}`))
      .finally(() => setBusy(false));
  };

  const adjGoals = (palette?.characters.find((c) => c.id === adjChar)?.dynamic_goals ?? []);

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
          <button className={`intervene-btn ${tool === 'event' ? 'active' : ''}`} onClick={() => openTool('event')} disabled={!simId || busy} title="注入环境事实/舞台事件">
            注入事件
          </button>
          <button className={`intervene-btn ${tool === 'expose' ? 'active' : ''}`} onClick={() => openTool('expose')} disabled={!simId || busy} title="把某事实曝光给目标角色">
            信息曝光
          </button>
          <button className={`intervene-btn ${tool === 'weight' ? 'active' : ''}`} onClick={() => openTool('weight')} disabled={!simId || busy} title="调整角色目标权重（必须填剧情内因）">
            调整权重
          </button>
        </div>

        {tool === 'event' && (
          <div className="intervene-form">
            <label className="if-label">注入什么（一句话舞台事件/环境变化）</label>
            <textarea
              className="if-input"
              rows={3}
              value={eventText}
              placeholder="例：窗外忽然传来急促的敲门声"
              onChange={(e) => setEventText(e.target.value)}
            />
            <button
              className="if-submit"
              disabled={!eventText.trim() || busy}
              onClick={() => {
                const text = eventText.trim();
                setEventText('');
                doIntervene('inject_event', { text }, `✓ 已注入事件：${text}`);
              }}
            >
              {busy ? '注入中…' : '注入'}
            </button>
          </div>
        )}

        {tool === 'expose' && (
          <div className="intervene-form">
            <label className="if-label">事实</label>
            <select
              className="if-select"
              value={expFact}
              onChange={(e) => setExpFact(e.target.value)}
            >
              <option value="">选择要曝光的事实…</option>
              {(palette?.facts ?? []).map((f) => (
                <option key={f.id} value={f.id}>{f.id} · {f.text}</option>
              ))}
            </select>
            <label className="if-label">曝光给</label>
            <select className="if-select" value={expChar} onChange={(e) => setExpChar(e.target.value)}>
              <option value="">选择目标角色…</option>
              {(palette?.characters ?? []).map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
            <label className="if-label">信道（角色怎么知道）</label>
            <select
              className="if-select"
              value={expChannel}
              onChange={(e) => setExpChannel(e.target.value as 'perceived' | 'told' | 'inferred')}
            >
              <option value="perceived">亲见</option>
              <option value="told">被告知</option>
              <option value="inferred">推测</option>
            </select>
            <button
              className="if-submit"
              disabled={!expFact || !expChar || busy}
              onClick={() => {
                const payload = { fact_id: expFact, target_char_id: expChar, channel: expChannel };
                setExpFact('');
                setExpChar('');
                doIntervene('expose', payload, '✓ 已曝光');
              }}
            >
              {busy ? '执行中…' : '曝光'}
            </button>
          </div>
        )}

        {tool === 'weight' && (
          <div className="intervene-form">
            <label className="if-label">角色</label>
            <select
              className="if-select"
              value={adjChar}
              onChange={(e) => {
                setAdjChar(e.target.value);
                setAdjGoal('');
              }}
            >
              <option value="">选择角色…</option>
              {(palette?.characters ?? []).map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
            <label className="if-label">动态目标</label>
            <select className="if-select" value={adjGoal} onChange={(e) => setAdjGoal(e.target.value)}>
              <option value="">选择目标…</option>
              {adjGoals.map((g) => (
                <option key={g.id} value={g.id}>{g.text}（权重 {Math.round(g.weight * 100)}%）</option>
              ))}
            </select>
            <label className="if-label">调整量</label>
            <div className="if-delta-row">
              <button className="if-delta" onClick={() => setAdjDelta((v) => Math.round((v - 0.1) * 10) / 10)}>−</button>
              <input
                className="if-delta-val"
                type="number"
                step={0.1}
                min={-0.5}
                max={0.5}
                value={adjDelta}
                onChange={(e) => setAdjDelta(Number(e.target.value) || 0)}
              />
              <button className="if-delta" onClick={() => setAdjDelta((v) => Math.round((v + 0.1) * 10) / 10)}>＋</button>
            </div>
            <label className="if-label">剧情内因（必填，§6.1 因果律）</label>
            <textarea
              className="if-input"
              rows={2}
              value={adjReason}
              placeholder="例：女儿深夜来电求救，家庭分量上升"
              onChange={(e) => setAdjReason(e.target.value)}
            />
            <button
              className="if-submit"
              disabled={!adjChar || !adjGoal || !adjReason.trim() || busy}
              title={adjReason.trim() ? '调整权重（内因将写回事件，角色可感知）' : '缺少剧情内因，无法提交'}
              onClick={() => {
                const payload = { char_id: adjChar, goal_id: adjGoal, delta: adjDelta, reason: adjReason.trim() };
                setAdjGoal('');
                setAdjReason('');
                doIntervene('adjust_weight', payload, '✓ 权重已调整（已写回内因）');
              }}
            >
              {busy ? '执行中…' : '调整'}
            </button>
          </div>
        )}

        {okMsg && <div className="intervene-msg ok">{okMsg}</div>}
        {err && <div className="intervene-msg err">{err}</div>}
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