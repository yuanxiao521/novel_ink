import type { AgentExecuted } from '../../api/novel';

/* ==========================================================================
   ToolResult —— 工具结果**富卡**（v1.43）

   动机（用户实测反馈）："点了体检/质检这些按钮没有输出，也不知道怎么体检的。"
   根因：结果只落一句摘要 + 右侧「质检」tab，作者停在「责编」时什么都看不到，
   而且摘要里没有明细。这里按工具类型把**它到底查了什么、结论是什么**摊开：
     体检 → 总评 + 问题清单（severity/建议）+ 口吻证据
     质检 → 账本对照（伏笔/信念/因果/状态）+ 风险 + 口吻风险
     评审 → 五维分 + 弱项（含正文原句证据）
     审稿会 → 四步进度 + 裁决 + 体检/质检/评审明细
   按钮与对话**共用同一张卡**（同一份数据形状），避免两处渲染迟早不一致。
   ========================================================================== */

type Rec = Record<string, unknown>;

const asRec = (v: unknown): Rec => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Rec) : {});
const asArr = (v: unknown): Rec[] => (Array.isArray(v) ? (v as Rec[]) : []);
const asStr = (v: unknown): string => (typeof v === 'string' ? v : v == null ? '' : String(v));
const asNum = (v: unknown): number | null => (typeof v === 'number' ? v : null);

const SEV_LABEL: Record<string, string> = { high: '高危', mid: '中等', low: '轻微' };
const DIM_LABEL: Record<string, string> = {
  tension: '张力', character: '人物', prose: '文笔', logic: '逻辑', hook: '钩子',
  pacing: '节奏', dialogue: '对白', imagery: '画面',
};

/** 一句「结论」——与后端 _summarize 同一口径（前端只在后端没给时兜底）。 */
function headline(tool: string, d: Rec): string {
  if (tool === 'prose.review') {
    const rep = asRec(d.report);
    const issues = asArr(rep.issues);
    const high = issues.filter((i) => asStr(i.severity) === 'high').length;
    return `查出 ${issues.length} 条问题（高危 ${high}）· 口吻 ${asArr(rep.voice_findings).length} 处`;
  }
  if (tool === 'prose.verify') {
    const op = asRec(d.opinion);
    return `伏笔推进 ${asArr(op.foreshadow_updates).length} · 信念变化 ${asArr(op.belief_deltas).length}`
      + ` · 因果 ${asArr(op.causal).length} · 状态变化 ${asArr(op.state_deltas).length} · 风险 ${asArr(op.risks).length}`;
  }
  if (tool === 'prose.quality_score') {
    const t = asNum(d.total);
    return t == null ? '正文为空，未评分' : `总分 ${t} · 弱项 ${asArr(d.weak_points).length} 条`;
  }
  if (tool === 'prose.review_meeting') return asStr(d.verdict);
  return '';
}

/** 问题清单（体检 / 口吻共用）。 */
function IssueList({ items, kind }: { items: Rec[]; kind: 'issue' | 'voice' }) {
  if (items.length === 0) return null;
  return (
    <div className="tr-list">
      {items.slice(0, 8).map((it, i) => (
        <div className="tr-item" key={i}>
          {kind === 'issue'
            ? <span className={`tr-sev ${asStr(it.severity) || 'mid'}`}>{SEV_LABEL[asStr(it.severity)] ?? asStr(it.severity) ?? '问题'}</span>
            : <span className="tr-sev voice">口吻</span>}
          <span className="tr-item-main">
            {kind === 'voice' && asStr(it.char) && <b>{asStr(it.char)}：</b>}
            {kind === 'voice' && asStr(it.evidence) && <em className="tr-quote">「{asStr(it.evidence).slice(0, 40)}」</em>}
            {kind === 'voice' ? asStr(it.issue) : asStr(it.text)}
            {asStr(it.suggestion) && <span className="tr-sug">→ {asStr(it.suggestion)}</span>}
          </span>
        </div>
      ))}
      {items.length > 8 && <div className="tr-more">…另有 {items.length - 8} 条，见下方审计记录</div>}
    </div>
  );
}

/** 体检明细块。 */
function ReviewBlock({ report, prior }: { report: Rec; prior: Rec }) {
  const issues = asArr(report.issues);
  const voice = asArr(report.voice_findings);
  const flags = asArr(prior.flags);
  return (
    <>
      {asStr(report.overall) && <div className="tr-overall">{asStr(report.overall)}</div>}
      <div className="tr-sub">问题清单 · {issues.length} 条</div>
      {issues.length === 0 ? <div className="tr-none">无问题</div> : <IssueList items={issues} kind="issue" />}
      <div className="tr-sub">口吻比对 · {voice.length} 处</div>
      {voice.length === 0 ? <div className="tr-none">各角色腔调都立得住</div> : <IssueList items={voice} kind="voice" />}
      {flags.length > 0 && (
        <>
          <div className="tr-sub">0-token 先验信号 · {flags.length} 条</div>
          <div className="tr-list">
            {flags.slice(0, 4).map((f, i) => <div className="tr-item" key={i}><span className="tr-sev prior">先验</span><span className="tr-item-main">{asStr(f.detail)}</span></div>)}
          </div>
        </>
      )}
    </>
  );
}

/** 质检明细块（账本对照）。 */
function VerifyBlock({ opinion }: { opinion: Rec }) {
  const risks = asArr(opinion.risks);
  const voice = asArr(opinion.voice_risks);
  const fs = asArr(opinion.foreshadow_updates);
  const bl = asArr(opinion.belief_deltas);
  const causal = asArr(opinion.causal);
  const st = asArr(opinion.state_deltas);
  return (
    <>
      <div className="tr-sub">风险提示 · {risks.length} 条</div>
      {risks.length === 0 ? <div className="tr-none">与账本无冲突</div> : (
        <div className="tr-list">
          {risks.slice(0, 8).map((r, i) => (
            <div className="tr-item" key={i}><span className="tr-sev high">风险</span><span className="tr-item-main">{asStr(r)}</span></div>
          ))}
        </div>
      )}
      {voice.length > 0 && (
        <>
          <div className="tr-sub">口吻风险（体检合并） · {voice.length} 条</div>
          <IssueList items={voice.map((v) => ({ ...v, issue: v.risk }))} kind="voice" />
        </>
      )}
      <div className="tr-sub">伏笔推进 · {fs.length} 条</div>
      {fs.length === 0 ? <div className="tr-none">未推进任何在册伏笔</div> : (
        <div className="tr-list">
          {fs.map((f, i) => (
            <div className="tr-item" key={i}><span className="tr-sev fs">{asStr(f.status) || '推进'}</span>
              <span className="tr-item-main">{asStr(f.text)}{asStr(f.reason) && <span className="tr-sug">（{asStr(f.reason)}）</span>}</span></div>
          ))}
        </div>
      )}
      <div className="tr-sub">信念变化 · {bl.length} 条</div>
      {bl.length === 0 ? <div className="tr-none">无认知变化</div> : (
        <div className="tr-list">
          {bl.map((b, i) => (
            <div className="tr-item" key={i}><span className="tr-sev bel">{asStr(b.channel) || '认知'}</span>
              <span className="tr-item-main"><b>{asStr(b.char)}：</b>{asStr(b.text)}</span></div>
          ))}
        </div>
      )}
      {(causal.length > 0 || st.length > 0) && (
        <>
          <div className="tr-sub">因果补记 {causal.length} · 状态变化 {st.length}</div>
          <div className="tr-list">
            {causal.slice(0, 4).map((c, i) => <div className="tr-item" key={`c${i}`}><span className="tr-sev causal">因果</span><span className="tr-item-main">{asStr(c)}</span></div>)}
            {st.slice(0, 6).map((s, i) => (
              <div className="tr-item" key={`s${i}`}><span className="tr-sev state">{asStr(s.kind) || '状态'}</span>
                <span className="tr-item-main"><b>{asStr(s.name)}</b>：{asStr(s.previous_value) && <em>{asStr(s.previous_value)} → </em>}{asStr(s.value)}</span></div>
            ))}
          </div>
        </>
      )}
    </>
  );
}

/** 评审明细块（五维 + 弱项证据）。 */
function ScoreBlock({ score }: { score: Rec }) {
  const total = asNum(score.total);
  const dims = asRec(score.scores);
  const weak = asArr(score.weak_points);
  return (
    <>
      <div className="tr-score">
        总分 <b>{total ?? '—'}</b>
        {Object.keys(dims).length > 0 && (
          <span className="tr-dims">
            {Object.entries(dims).map(([k, v]) => (
              <span className={`tr-dim${typeof v === 'number' && v < 70 ? ' low' : ''}`} key={k}>
                {DIM_LABEL[k] ?? k} {asStr(v)}
              </span>
            ))}
          </span>
        )}
      </div>
      {weak.length > 0 && (
        <>
          <div className="tr-sub">弱项 · {weak.length} 条（含正文原句证据）</div>
          <div className="tr-list">
            {weak.slice(0, 5).map((w, i) => (
              <div className="tr-item" key={i}><span className="tr-sev mid">{asStr(w.dim) || '弱项'}</span>
                <span className="tr-item-main">{asStr(w.why)}{asStr(w.evidence) && <em className="tr-quote">「{asStr(w.evidence).slice(0, 36)}」</em>}</span></div>
            ))}
          </div>
        </>
      )}
    </>
  );
}

/** 候选文本块（润色/初稿/回环/审稿会共用）。 */
function TextBlock({ text, onApplyText, note }: { text: string; onApplyText?: (t: string) => void; note?: string }) {
  if (!text.trim()) return null;
  return (
    <div className="tr-cand">
      <div className="tr-cand-head">{note ?? `候选 ${text.length} 字`}（未落库，采纳后点「保存」才写库）</div>
      <div className="tr-cand-text">{text.slice(0, 220)}{text.length > 220 ? '……' : ''}</div>
      {onApplyText && (
        <button className="ws-btn ws-btn-primary" onClick={() => onApplyText(text)}>采纳到正文</button>
      )}
    </div>
  );
}

export interface ToolResultProps {
  tool: string;
  data: Rec;
  onApplyText?: (t: string) => void;
  /** 已从 executed 里取到的结构化结果（审稿会走 job.result）。 */
  executed?: AgentExecuted;
}

export function ToolResult({ tool, data, onApplyText }: ToolResultProps) {
  const d = asRec(data);
  const head = headline(tool, d);
  const candidate = asStr(d.after) || asStr(d.text) || asStr(d.candidate);

  if (tool === 'prose.review_meeting') {
    const steps = asArr(d.steps);
    const review = asRec(d.review);
    const verify = asRec(d.verify);
    const score = asRec(d.score);
    return (
      <div className="tr-card">
        <div className="tr-head">🗂 审稿会 <span className="tr-head-sub">体检 ∥ 质检 → 润色（按需）→ 评审</span></div>
        {steps.length > 0 && (
          <div className="tr-steps">
            {steps.map((s, i) => (
              <span className="tr-step" key={i}>
                {asStr(s.step)}
                {asNum(s.total) != null && ` ${asNum(s.total)} 分`}
                {asNum(s.issues) != null && ` 问题 ${asNum(s.issues)}`}
                {asNum(s.voice) != null && ` 口吻 ${asNum(s.voice)}`}
                {asNum(s.risks) != null && ` 风险 ${asNum(s.risks)}`}
                {asNum(s.foreshadow) != null && ` 伏笔 ${asNum(s.foreshadow)}`}
                {asNum(s.beliefs) != null && ` 信念 ${asNum(s.beliefs)}`}
              </span>
            ))}
          </div>
        )}
        {asStr(d.verdict) && <div className="tr-verdict">裁决：{asStr(d.verdict)}</div>}
        <details className="tr-fold" open>
          <summary>🩺 体检明细</summary>
          <ReviewBlock report={asRec(review.report)} prior={asRec(review.voice_prior)} />
        </details>
        <details className="tr-fold">
          <summary>🔍 质检明细</summary>
          <VerifyBlock opinion={asRec(verify.opinion)} />
        </details>
        <details className="tr-fold">
          <summary>📊 评审明细</summary>
          <ScoreBlock score={score} />
        </details>
        <TextBlock text={candidate} onApplyText={onApplyText} note="润色候选" />
      </div>
    );
  }

  if (tool === 'prose.review') {
    return (
      <div className="tr-card">
        <div className="tr-head">🩺 体检报告{head && <span className="tr-head-sub">{head}</span>}</div>
        <ReviewBlock report={asRec(d.report)} prior={asRec(d.voice_prior)} />
      </div>
    );
  }

  if (tool === 'prose.verify') {
    return (
      <div className="tr-card">
        <div className="tr-head">🔍 质检意见{head && <span className="tr-head-sub">{head}</span>}</div>
        <VerifyBlock opinion={asRec(d.opinion)} />
      </div>
    );
  }

  if (tool === 'prose.quality_score') {
    return (
      <div className="tr-card">
        <div className="tr-head">📊 评审结果</div>
        <ScoreBlock score={d} />
        {asStr(d.note) && <div className="tr-none">{asStr(d.note)}</div>}
      </div>
    );
  }

  if (tool === 'prose.scan_tone') {
    const rules = asArr(asRec(d.report).rules);
    return (
      <div className="tr-card">
        <div className="tr-head">🧪 AI 味扫描 <span className="tr-head-sub">0-token 规则命中 {rules.length} 条</span></div>
        {rules.length === 0 ? <div className="tr-none">没有命中任何规则</div> : (
          <div className="tr-list">
            {rules.slice(0, 10).map((r, i) => (
              <div className="tr-item" key={i}><span className={`tr-sev ${asStr(r.severity) === 'violation' ? 'high' : 'mid'}`}>{asStr(r.rule)}</span>
                <span className="tr-item-main">{asStr(r.detail)}{asStr(r.evidence) && <em className="tr-quote">「{asStr(r.evidence).slice(0, 30)}」</em>}</span></div>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (tool === 'prose.spot_fix') {
    const applied = asArr(d.changes).filter((c) => c.applied);
    return (
      <div className="tr-card">
        <div className="tr-head">🔧 定点修复
          <span className="tr-head-sub">命中 {asStr(d.hits_before) || '—'} → {asStr(d.hits_after) || '—'} · {d.accepted ? `采纳 ${applied.length} 处` : '未采纳'}</span>
        </div>
        {!d.accepted && <div className="tr-none">未采纳：{asStr(d.reason) || asStr(d.skipped) || '双闸未通过'}</div>}
        {applied.length > 0 && (
          <div className="tr-list">
            {applied.slice(0, 6).map((c, i) => (
              <div className="tr-item" key={i}><span className="tr-sev fs">改</span>
                <span className="tr-item-main"><em className="tr-quote">{asStr(c.before).slice(0, 30)}</em> → {asStr(c.after).slice(0, 30)}
                  {asStr(c.reason) && <span className="tr-sug">（{asStr(c.reason)}）</span>}</span></div>
            ))}
          </div>
        )}
        <TextBlock text={candidate} onApplyText={onApplyText} note="修复稿" />
      </div>
    );
  }

  if (tool === 'prose.quality_loop') {
    const before = asNum(asRec(d.before).total);
    const after = asNum(asRec(d.after_score).total);
    const applied = asArr(d.changes).filter((c) => c.applied);
    return (
      <div className="tr-card">
        <div className="tr-head">♻ 质量回环
          <span className="tr-head-sub">
            {before != null || after != null ? `${before ?? '—'} → ${after ?? '—'} 分` : '未评分'}
            {asStr(d.route) && ` · ${asStr(d.route) === 'spot' ? '定点' : '全文'}路由`}
          </span>
        </div>
        {!d.accepted && <div className="tr-none">未采纳：{asStr(d.reason) || asStr(d.skipped) || '评分达标或未改动'}</div>}
        {applied.length > 0 && (
          <div className="tr-list">
            {applied.slice(0, 6).map((c, i) => (
              <div className="tr-item" key={i}><span className="tr-sev fs">改</span>
                <span className="tr-item-main">{asStr(c.reason) || asStr(c.before).slice(0, 40)}</span></div>
            ))}
          </div>
        )}
        <TextBlock text={candidate} onApplyText={onApplyText} note="回环改稿" />
      </div>
    );
  }

  if (tool === 'prose.polish' || tool === 'prose.draft') {
    const label = tool === 'prose.polish' ? '🎨 润色' : '✍ 写手 · 初稿';
    return (
      <div className="tr-card">
        <div className="tr-head">{label}<span className="tr-head-sub">{asStr(d.summary) || `${candidate.length} 字`}</span></div>
        <TextBlock text={candidate} onApplyText={onApplyText} />
      </div>
    );
  }

  // 兜底：认识的字段就展示，不认识就指向审计记录（不编造）
  const generic = head || asStr(d.summary) || asStr(d.note);
  return (
    <div className="tr-card">
      <div className="tr-head">{tool}{generic && <span className="tr-head-sub">{generic}</span>}</div>
      <TextBlock text={candidate} onApplyText={onApplyText} />
      {!generic && !candidate && <div className="tr-none">明细见下方审计记录。</div>}
    </div>
  );
}
