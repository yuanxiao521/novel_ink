import { useEffect, useState } from 'react';
import { fetchSceneScript, sceneScriptUrl } from '../../api/novel';
import type { SceneScriptResult, ScriptTurn } from '../../api/novel';

const CLS_LABEL: Record<string, string> = {
  'type-conflict': '冲突',
  'type-dialogue': '对话',
  'type-action': '行动',
  'type-info': '信息',
};

/**
 * S4 剧本台：把"推演回合"当剧本产物看（不再转叙述正文）。
 *  - 回合事件流：角色：台词 /（角色 动作）/ 摘要 / 可选张力标注
 *  - 每回合 ▸ 思考 小箭头展开：内心独白 / 动机推理 / 情绪 分层显示
 *  - 一键导出 Markdown 剧本（浏览器直开）
 */
export function ScriptView({ sceneId, onClose }: { sceneId: string; onClose: () => void }) {
  const [data, setData] = useState<SceneScriptResult | null>(null);
  const [tension, setTension] = useState(false);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [err, setErr] = useState('');

  useEffect(() => {
    setErr('');
    fetchSceneScript(sceneId, { thoughts: true, tension })
      .then(setData)
      .catch((e) => setErr('剧本加载失败：' + (e instanceof Error ? e.message : String(e))));
  }, [sceneId, tension]);

  const turns: ScriptTurn[] = data?.json?.turns ?? [];
  const sceneTitle = String((data?.json?.scene as Record<string, unknown> | undefined)?.title ?? '');

  return (
    <div className="studio-overlay">
      <div className="studio-panel">
        <header className="studio-header">
          <div className="studio-title">
            <span className="studio-seal">剧</span>
            <div>
              <div className="studio-name">剧本台 · {sceneTitle || '（未命名场景）'}</div>
              <div className="studio-sub">推演回合即剧本产物（台词/动作/思考分层留档）</div>
            </div>
          </div>
          <button className="studio-close" title="关闭" onClick={onClose}>
            ✕
          </button>
        </header>

        <div className="studio-toolbar">
          <button className="studio-btn" onClick={() => setTension((v) => !v)}>
            {tension ? '隐藏张力标注' : '显示张力标注'}
          </button>
          <a className="studio-btn" href={sceneScriptUrl(sceneId, tension)} target="_blank" rel="noreferrer">
            ⬇ 导出剧本 Markdown
          </a>
          <span className="studio-metric">{turns.length} 回合</span>
        </div>

        {err && <div className="studio-empty-hint">{err}</div>}
        {!err && turns.length === 0 && (
          <div className="studio-empty-hint">
            本场景尚无推演回合 —— 回工作台跑几步推演，这里就会出现剧本。
          </div>
        )}

        <div className="script-body">
          {turns.map((t) => (
            <div className="script-turn" key={t.turn}>
              <div className="script-turn-head">
                <span className="script-turn-no">
                  〔回合 {t.turn} · {CLS_LABEL[t.cls] ?? t.cls}〕
                </span>
                {tension && (
                  <span className="studio-metric">
                    张力 {t.tension ?? '—'} {t.tension_trend ?? ''}
                  </span>
                )}
                {(t.thoughts?.length ?? 0) > 0 && (
                  <button
                    className="script-think-toggle"
                    title="展开/收起本回合各角色思考"
                    onClick={() => setExpanded((o) => ({ ...o, [t.turn]: !o[t.turn] }))}
                  >
                    {expanded[t.turn] ? '▾' : '▸'} 思考 {t.thoughts.length}
                  </button>
                )}
              </div>

              {t.summary && <div className="script-summary">· {t.summary}</div>}
              {(t.events ?? []).map((e, i) => (
                <div className="script-line" key={i}>
                  {e.kind === 'dialogue'
                    ? (e.actor ?? '') + '：' + (e.text ?? '')
                    : '（' + (e.actor ?? '') + ' ' + (e.text ?? '') + '）'}
                </div>
              ))}

              {expanded[t.turn] &&
                (t.thoughts ?? []).map((th, i) => (
                  <div className="script-thought" key={i}>
                    <div className="script-thought-char">{th.char ?? '（未知角色）'}</div>
                    {th.monologue && (
                      <div className="script-thought-row">
                        <span className="script-tag">内心独白</span>
                        {th.monologue}
                      </div>
                    )}
                    {th.reasoning && (
                      <div className="script-thought-row">
                        <span className="script-tag">动机/推理</span>
                        {th.reasoning}
                      </div>
                    )}
                    {th.emotion && (
                      <div className="script-thought-row">
                        <span className="script-tag">情绪</span>
                        {th.emotion}
                      </div>
                    )}
                  </div>
                ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
