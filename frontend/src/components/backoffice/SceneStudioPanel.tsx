import { useCallback, useEffect, useState } from 'react';
import {
  fetchSceneDetail,
  generateSceneDraft,
  reviewSceneProse,
  polishSceneProse,
  verifySceneProse,
  listProseNotes,
  approveProseNote,
  rejectProseNote,
  saveSceneProse,
  scanAiTone,
  spotFixProse,
  runQualityLoop,
} from '../../api/novel';
import type {
  AiToneReport,
  ProseNote,
  QualityLoopResult,
  SceneDetail,
  SpotFixResult,
  VerifyOpinion,
} from '../../api/novel';

const DIM_LABEL: Record<string, string> = {
  coherence: '连贯',
  character: '人物',
  pacing: '节奏',
  imagery: '画面',
  ending: '收尾',
};

const scoreCls = (v: number) => (v >= 80 ? 'good' : v >= 70 ? 'mid' : 'bad');

/* ---------- 常量 ---------- */

const KIND_LABEL: Record<string, string> = {
  writer: '✍ 写手',
  editor: '🩺 体检员',
  polisher: '🎨 润色师',
  verifier: '🔍 质检员',
};

const STATUS_LABEL: Record<string, string> = {
  pending: '待审阅',
  approved: '已批准',
  rejected: '已驳回',
};

const STATUS_CLS: Record<string, string> = {
  pending: 'studio-note-pending',
  approved: 'studio-note-approved',
  rejected: 'studio-note-rejected',
};

interface Props {
  sceneId: string;
  onClose: () => void;
}

/** 审计记录明细（payload_json）：把 S2 口吻/先验与 A3 反 AI 味的落库明细显示出来。 */
function NoteDetails({ note }: { note: ProseNote }) {
  const raw = note.payload_json;
  if (!raw) return null;
  let p: Record<string, unknown>;
  try {
    p = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return null;
  }
  const asArr = (v: unknown): Array<Record<string, unknown>> =>
    Array.isArray(v) ? (v as Array<Record<string, unknown>>) : [];
  const parts: string[] = [];

  const vf = asArr(p.voice_findings);
  if (vf.length) {
    parts.push(
      `口吻检点 ${vf.length}：` +
        vf.slice(0, 2).map((v) => `${String(v.char ?? '')}（${String(v.issue ?? '').slice(0, 16)}）`).join('；'),
    );
  }
  const vr = asArr(p.voice_risks);
  if (vr.length) parts.push(`质检口吻风险 ${vr.length}`);

  const vp = (p.voice_prior ?? null) as { per_char?: unknown } | null;
  const per = asArr(vp?.per_char).filter((m) => Number(m.dialogues ?? 0) > 0);
  if (per.length) {
    parts.push('台词分账 ' + per.map((m) => `${String(m.name ?? '')} ${String(m.avg_len ?? '')} 字/句`).join('，'));
  }
  const at = (p.ai_tone ?? null) as { counts?: { total?: number } } | null;
  if (at?.counts) parts.push(`AI 味命中 ${at.counts.total ?? 0} 条`);
  const ch = asArr(p.changes);
  if (ch.length) parts.push(`改写 ${ch.filter((c) => c.applied).length}/${ch.length} 句`);

  if (!parts.length) return null;
  return <div className="studio-note-detail">{parts.join(' · ')}</div>;
}

/* ---------- 组件 ---------- */

export function SceneStudioPanel({ sceneId, onClose }: Props) {
  const [scene, setScene] = useState<SceneDetail | null>(null);
  const [text, setText] = useState('');
  const [notes, setNotes] = useState<ProseNote[]>([]);
  const [busy, setBusy] = useState<'' | 'draft' | 'review' | 'polish' | 'verify' | 'save' | 'note' | 'aitone' | 'spotfix' | 'quality'>('');
  const [report, setReport] = useState<{ issues: Array<{ severity: string; text: string; suggestion: string }>; overall: string } | null>(null);
  const [polish, setPolish] = useState<{ after: string; summary: string } | null>(null);
  const [opinion, setOpinion] = useState<VerifyOpinion | null>(null);
  const [aiTone, setAiTone] = useState<AiToneReport | null>(null);
  const [spot, setSpot] = useState<SpotFixResult | null>(null);
  const [quality, setQuality] = useState<QualityLoopResult | null>(null);
  const qualityWeak = quality?.before?.weak_points ?? [];   // 派生值：避免 JSX 内失窄化
  const [toast, setToast] = useState('');

  const flash = (t: string) => {
    setToast(t);
    window.setTimeout(() => setToast(''), 1800);
  };

  const load = useCallback(() => {
    void (async () => {
      try {
        const d = await fetchSceneDetail(sceneId);
        setScene(d);
        setText(d.final_prose ?? '');
      } catch {
        /* 保持现状 */
      }
      try {
        setNotes(await listProseNotes(sceneId));
      } catch {
        /* 保持现状 */
      }
    })();
  }, [sceneId]);

  useEffect(() => { load(); }, [load]);

  const run = async (kind: NonNullable<typeof busy>, fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(kind);
    setReport(null); setPolish(null); setOpinion(null);
    try {
      await fn();
      setNotes(await listProseNotes(sceneId));
    } catch (e) {
      flash(`操作失败：${String(e)}`);
    } finally {
      setBusy('');
    }
  };

  const onDraft = () => void run('draft', async () => {
    const r = await generateSceneDraft(sceneId);
    if (r.text) { setText(r.text); flash('写手已生成初稿'); }
    else flash('模型未接入，写手暂无法生成');
  });

  const onReview = () => void run('review', async () => {
    const r = await reviewSceneProse(sceneId, text);
    setReport(r.report);
    flash('体检完成（审计记录待审阅）');
  });

  const onPolish = () => void run('polish', async () => {
    const r = await polishSceneProse(sceneId, text);
    setPolish(r);
    flash('润色完成（审计记录待审阅）');
  });

  const onVerify = () => void run('verify', async () => {
    const r = await verifySceneProse(sceneId, text);
    setOpinion(r.opinion);
    flash('质检完成（确认后才记账）');
  });

  const onQuality = () => void run('quality', async () => {
    const r = await runQualityLoop(sceneId, text);
    setQuality(r);
    if (r.accepted) flash('质量回环：已改写并采纳（可查看分数卡）');
    else if (r.skipped === 'score-ok') flash('质量回环：分数达标，未改动');
    else flash('质量回环：未采纳（见分数卡原因）');
  });

  const onAiTone = () => void run('aitone', async () => {
    const r = await scanAiTone(sceneId, text);
    setAiTone(r.report);
    flash(r.report.clean ? 'AI 味扫描：未发现规则命中' : `AI 味扫描：命中 ${r.report.counts.total} 条`);
  });

  const onSpotFix = () => void run('spotfix', async () => {
    const r = await spotFixProse(sceneId, text);
    setSpot(r);
    if (r.accepted) {
      flash(`定点修复：改 ${r.changes.filter((c) => c.applied).length} 句（命中 ${r.hits_before}→${r.hits_after}）`);
    } else if (r.skipped === 'no-fixable-hit') {
      flash('没有可改写的命中句（标点类只提示不改）');
    } else {
      flash(`未采纳：${(r.reason || r.error || '见下方说明').slice(0, 40)}`);
    }
  });

  const onSave = () => void run('save', async () => {
    if (!text.trim()) { flash('正文为空'); return; }
    const r = await saveSceneProse(sceneId, text);
    flash(r.unchanged ? '正文未变化' : `正文已保存（${r.word_count} 字）`);
  });

  const onNoteAction = (noteId: string, approve: boolean) => void run('note', async () => {
    if (approve) await approveProseNote(noteId);
    else await rejectProseNote(noteId);
    flash(approve ? '已批准（验证明细已记账）' : '已驳回');
  });

  return (
    <div className="studio-overlay">
      <div className="studio-panel">
        {toast && <div className="studio-toast">{toast}</div>}
        <header className="studio-header">
          <div className="studio-title">
            <span className="studio-seal">墨</span>
            <div>
              <div className="studio-name">{scene?.title ?? '正文协作'}</div>
              {scene && (
                <div className="studio-sub">
                  {scene.stage_desc ? `舞台：${scene.stage_desc}` : '（未布置舞台）'}
                </div>
              )}
            </div>
          </div>
          <button className="studio-close" title="关闭" onClick={onClose}>✕</button>
        </header>

        {scene && (
          <div className="studio-scene-meta">
            {scene.goal && <span className="studio-meta-item">🎯 {scene.goal}</span>}
            {scene.content_desc && <span className="studio-meta-item">📜 {scene.content_desc}</span>}
          </div>
        )}

        {/* 正文编辑区 */}
        <div className="studio-editor">
          <textarea
            className="studio-textarea"
            placeholder="正文会出现在这里：写手生成 / 作者手写 / 应用润色稿……"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="studio-editor-footer">
            <span className="studio-wordcount">{text.length} 字</span>
            <button className="studio-btn" onClick={onSave} disabled={busy !== ''}>
              💾 保存正文
            </button>
          </div>
        </div>

        {/* 四角色工具栏 */}
        <div className="studio-toolbar">
          <button className="studio-btn studio-btn-primary" disabled={busy !== ''}
            onClick={onDraft}>{busy === 'draft' ? '写手中…' : '✍ 写手 · 初稿'}</button>
          <button className="studio-btn" disabled={busy !== '' || !text.trim()}
            onClick={onReview}>{busy === 'review' ? '体检中…' : '🩺 体检'}</button>
          <button className="studio-btn" disabled={busy !== '' || !text.trim()}
            onClick={onPolish}>{busy === 'polish' ? '润色中…' : '🎨 润色'}</button>
          <button className="studio-btn" disabled={busy !== '' || !text.trim()}
            onClick={onVerify}>{busy === 'verify' ? '质检中…' : '🔍 质检'}</button>
          <button className="studio-btn" disabled={busy !== '' || !text.trim()}
            onClick={onAiTone}>{busy === 'aitone' ? '扫描中…' : '🧹 AI 味扫描'}</button>
          <button className="studio-btn" disabled={busy !== '' || !text.trim()}
            onClick={onSpotFix}>{busy === 'spotfix' ? '修复中…' : '🪄 定点修复'}</button>
          <button className="studio-btn studio-btn-primary" disabled={busy !== '' || !text.trim()}
            onClick={onQuality}>{busy === 'quality' ? '评估中…' : '🎯 质量回环'}</button>
        </div>

        {/* 最近产出 */}
        {report && (
          <div className="studio-role-out">
            <div className="studio-role-title">🩺 体检报告</div>
            <div className="studio-role-overall">{report.overall}</div>
            {report.issues.length === 0 && <div className="studio-empty-hint">（未发现问题）</div>}
            {report.issues.map((it, i) => (
              <div className="studio-issue" key={i}>
                <span className={`studio-sev studio-sev-${it.severity}`}>{it.severity}</span>
                <span className="studio-issue-text">{it.text}</span>
                {it.suggestion && <span className="studio-issue-sug">→ {it.suggestion}</span>}
              </div>
            ))}
          </div>
        )}

        {polish && (
          <div className="studio-role-out">
            <div className="studio-role-title">🎨 润色结果</div>
            <div className="studio-role-overall">{polish.summary}</div>
            <div className="studio-polish-preview">{polish.after.slice(0, 200)}{polish.after.length > 200 ? '……' : ''}</div>
            <div className="studio-inline-actions">
              <button className="studio-btn studio-btn-primary" onClick={() => { setText(polish.after); flash('已应用润色稿'); }}>
                ✓ 应用润色稿
              </button>
              <button className="studio-btn" onClick={() => setPolish(null)}>忽略</button>
            </div>
          </div>
        )}

        {quality && (
          <div className="studio-role-out quality-card">
            <div className="quality-head">
              <div className="quality-title">🎯 质量回环</div>
              <div className={`quality-score ${scoreCls(quality.after_score?.total ?? quality.before?.total ?? 0)}`}>
                <span className="quality-score-num">{quality.before?.total ?? '—'}</span>
                {quality.after_score && (
                  <>
                    <span className="quality-arrow">→</span>
                    <span className="quality-score-num">{quality.after_score.total}</span>
                  </>
                )}
              </div>
            </div>
            <div className="quality-verdict">
              {quality.accepted
                ? `✓ 已采纳（${quality.route === 'spot' ? '定点' : '全文'}重写 · ${quality.rewrites} 次）`
                : quality.skipped === 'score-ok'
                  ? `未触发：${quality.reason ?? '分数达标'}`
                  : `已回退：${quality.reason ?? quality.error ?? '未采纳'}`}
            </div>
            {quality.before?.scores && (
              <div className="quality-dims">
                {Object.entries(quality.before.scores).map(([k, v]) => {
                  const av = quality.after_score?.scores?.[k];
                  return (
                    <div className="quality-dim" key={k}>
                      <span className="quality-dim-name">{DIM_LABEL[k] ?? k}</span>
                      <div className="quality-bar">
                        <div className={`quality-bar-fill ${scoreCls(v)}`} style={{ width: (v + '%') }} />
                      </div>
                      <span className="quality-dim-val">
                        {v}
                        {typeof av === 'number' && av !== v ? ` → ${av}` : ''}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
            {qualityWeak.length > 0 && (
              <div className="quality-weaks">
                {qualityWeak.slice(0, 3).map((w, i) => (
                  <div className="quality-weak" key={i}>
                    <span className="quality-weak-dim">{DIM_LABEL[w.dim ?? ''] ?? w.dim}</span>
                    <span className="quality-weak-ev">「{w.evidence}」</span>
                    {w.why && <span className="quality-weak-why">{w.why}</span>}
                  </div>
                ))}
              </div>
            )}
            {quality.accepted && (
              <div className="studio-inline-actions">
                <button
                  className="studio-btn studio-btn-primary"
                  onClick={() => {
                    setText(quality.after);
                    setQuality(null);
                    flash('已应用质量回环改写');
                  }}
                >
                  ✓ 应用改写
                </button>
                <button className="studio-btn" onClick={() => setQuality(null)}>忽略</button>
              </div>
            )}
          </div>
        )}

        {aiTone && (
          <div className="studio-role-out">
            <div className="studio-role-title">🧹 AI 味扫描（0-token 确定性规则）</div>
            <div className="studio-role-overall">
              {aiTone.clean
                ? '未发现规则命中'
                : `命中 ${aiTone.counts.total} 条（严重 ${aiTone.counts.violation}）`}
              <span className="studio-metric">
                破折号 {aiTone.metrics.dash} · 省略号 {aiTone.metrics.ellipsis} · 填充词{' '}
                {aiTone.metrics.filler_per_100}/百字
              </span>
            </div>
            {aiTone.rules.map((r, i) => (
              <div className="studio-issue" key={`${r.rule}-${i}`}>
                <span className={`studio-sev studio-sev-${r.severity === 'violation' ? 'high' : 'mid'}`}>
                  {r.rule}
                </span>
                <span className="studio-issue-text">{r.detail}</span>
              </div>
            ))}
          </div>
        )}

        {spot && (
          <div className="studio-role-out">
            <div className="studio-role-title">🪄 定点修复{spot.accepted ? '' : '（未采纳）'}</div>
            <div className="studio-role-overall">
              {spot.accepted
                ? `已改 ${spot.changes.filter((c) => c.applied).length} 句；句子级命中 ${spot.hits_before}→${spot.hits_after}`
                : spot.skipped === 'no-fixable-hit'
                  ? '没有"可改写"的命中句（破折号等标点类只提示不改）'
                  : spot.reason || spot.error || '未采纳'}
            </div>
            {spot.changes
              .filter((c) => c.applied)
              .map((c, i) => (
                <div className="studio-diff-row" key={i}>
                  <div className="studio-diff-before">− {c.before}</div>
                  <div className="studio-diff-after">＋ {c.after}</div>
                  {c.reason && <div className="studio-diff-reason">{c.reason}</div>}
                </div>
              ))}
            {spot.changes
              .filter((c) => !c.applied && c.blocked_reason)
              .slice(0, 3)
              .map((c, i) => (
                <div className="studio-issue" key={`b-${i}`}>
                  <span className="studio-sev studio-sev-low">丢弃</span>
                  <span className="studio-issue-text">{c.blocked_reason}</span>
                </div>
              ))}
            {spot.accepted && (
              <div className="studio-inline-actions">
                <button
                  className="studio-btn studio-btn-primary"
                  onClick={() => {
                    setText(spot.after);
                    setSpot(null);
                    flash('已应用定点修复');
                  }}
                >
                  ✓ 应用定点修复
                </button>
                <button className="studio-btn" onClick={() => setSpot(null)}>忽略</button>
              </div>
            )}
          </div>
        )}

        {opinion && (
          <div className="studio-role-out">
            <div className="studio-role-title">🔍 质检意见（确认后记账）</div>
            {opinion.foreshadow_updates.length === 0 && opinion.belief_deltas.length === 0 && opinion.risks.length === 0 && (
              <div className="studio-empty-hint">（未发现需记账的变化）</div>
            )}
            {opinion.foreshadow_updates.map((f, i) => (
              <div className="studio-opinion-row" key={`f-${i}`}>伏笔 → {f.status}：{f.text}{f.reason ? `（${f.reason}）` : ''}</div>
            ))}
            {opinion.belief_deltas.map((b, i) => (
              <div className="studio-opinion-row" key={`b-${i}`}>信念[{b.char}]：{b.text}</div>
            ))}
            {opinion.causal.map((c, i) => (
              <div className="studio-opinion-row" key={`c-${i}`}>因果：{c}</div>
            ))}
            {opinion.risks.map((r, i) => (
              <div className="studio-opinion-row studio-risk" key={`r-${i}`}>风险：{r}</div>
            ))}
            <div className="studio-inline-actions">
              <button className="studio-btn studio-btn-primary" onClick={onSave} disabled={busy !== ''}>
                ✅ 批准并记账
              </button>
            </div>
          </div>
        )}

        {/* 审计记录（可追溯） */}
        <div className="studio-notes">
          <div className="studio-notes-title">审计记录（可追溯）</div>
          {notes.length === 0 && <div className="studio-empty-hint">暂无记录</div>}
          {notes.map((n) => (
            <div className={`studio-note ${STATUS_CLS[n.status] ?? ''}`} key={n.id}>
              <div className="studio-note-top">
                <span className="studio-note-kind">{KIND_LABEL[n.kind] ?? n.kind}</span>
                <span className="studio-note-status">{STATUS_LABEL[n.status] ?? n.status}</span>
                <span className="studio-note-time">{new Date(n.ts).toLocaleString()}</span>
              </div>
              <div className="studio-note-sug">{n.suggestion}</div>
              <NoteDetails note={n} />
              {n.status === 'pending' && (
                <div className="studio-inline-actions">
                  {n.kind === 'verifier' && (
                    <button className="studio-btn studio-btn-primary" disabled={busy !== ''}
                      onClick={() => onNoteAction(n.id, true)}>批准 · 记账</button>
                  )}
                  <button className="studio-btn" disabled={busy !== ''}
                    onClick={() => onNoteAction(n.id, false)}>驳回</button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}