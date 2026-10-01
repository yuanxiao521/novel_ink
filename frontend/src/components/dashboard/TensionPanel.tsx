import type { GlobalView } from '../../api/novel';

const SEV_CLASS: Record<string, string> = { high: 'danger', mid: 'warn', low: 'info' };

/**
 * S3 全局张力面板（零依赖，纯 SVG）：
 *  - 曲线：张力按章绘制，**缺口断开**（不假装连线）；点上 title 带章号与数值；
 *  - 诊断：R1-R7 带扣分与证据（可解释）；
 *  - 伏笔：呼应网络条数与逾期前 3 条。
 * 无张力数据时不画假曲线，如实提示"去导演台跑一次推演"。
 */
export function TensionPanel({ view }: { view: GlobalView | null }) {
  if (!view) return <div className="char-empty">张力视图加载中…</div>;
  const { curve, summary, diagnostics, foreshadows, structure_score: score } = view;

  const W = 640;
  const H = 170;
  const PAD = 26;
  const span = W - PAD * 2;
  const xOf = (i: number) => (curve.length <= 1 ? PAD + span / 2 : PAD + (i * span) / (curve.length - 1));
  const yOf = (v: number) => H - PAD - (Math.max(0, Math.min(100, v)) / 100) * (H - PAD * 2);

  type Pt = { i: number; x: number; y: number; v: number };
  const runs: Pt[][] = [];
  let run: Pt[] = [];
  curve.forEach((c, i) => {
    if (c.tension_avg == null) {
      if (run.length) runs.push(run);
      run = [];
      return;
    }
    run.push({ i, x: xOf(i), y: yOf(c.tension_avg), v: c.tension_avg });
  });
  if (run.length) runs.push(run);

  return (
    <section className="section-block tension-section">
      <div className="section-header">
        <h2 className="section-title">
          <span className="title-icon">〽</span>全局张力
        </h2>
        <span className={'tension-score ' + (score < 70 ? 'warn' : '')}>结构分 {score}</span>
      </div>

      {summary.valid === 0 ? (
        <div className="char-empty">
          尚无推演张力数据 —— 进「导演台」跑一次推演即可生成曲线
          {diagnostics.length > 0 && <div className="tension-hint">{diagnostics[0].detail}</div>}
        </div>
      ) : (
        <>
          <div className="tension-summary">
            <span>有效章 {summary.valid}</span>
            <span>均值 {summary.mean}</span>
            <span>波动 {summary.std ?? '—'}</span>
            {summary.peak_chapter && (
              <span>峰值 第{summary.peak_chapter.order_no}章（{summary.peak_chapter.tension_avg}）</span>
            )}
            <span>伏笔 埋 {summary.foreshadows_open} / 收 {summary.foreshadows_closed}</span>
          </div>
          <svg className="tension-svg" viewBox={'0 0 ' + W + ' ' + H} role="img" aria-label="全书张力曲线">
            <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} className="tension-axis" />
            <line x1={PAD} y1={PAD} x2={W - PAD} y2={PAD} className="tension-axis faint" />
            {runs.map((r, ri) => (
              <polyline
                key={ri}
                className="tension-line"
                points={r.map((p) => p.x + ',' + p.y).join(' ')}
              />
            ))}
            {runs.flat().map((p) => (
              <circle key={p.i} cx={p.x} cy={p.y} r={4} className="tension-dot">
                <title>{'第' + curve[p.i].order_no + '章 ' + curve[p.i].title + '：张力 ' + p.v}</title>
              </circle>
            ))}
            {curve.map((c, i) => (
              <text key={c.chapter_id} x={xOf(i)} y={H - 8} className="tension-x-label">
                {c.order_no}
              </text>
            ))}
          </svg>
        </>
      )}

      {diagnostics.length > 0 && (
        <div className="diag-list">
          {diagnostics.map((d) => (
            <div className={'diag-item ' + (SEV_CLASS[d.severity] ?? 'info')} key={d.rule}>
              <span className="diag-rule">{d.rule}</span>
              <div className="diag-body">
                <div className="diag-title">
                  {d.title}
                  <span className="diag-deduct">−{d.deduct}</span>
                </div>
                <div className="diag-detail">{d.detail}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {foreshadows.nodes.length > 0 && (
        <div className="fs-net">
          <div className="fs-net-head">伏笔呼应网络 · {foreshadows.nodes.length} 条</div>
          {foreshadows.overdue.length > 0 ? (
            <ul className="fs-overdue">
              {foreshadows.overdue.slice(0, 3).map((n) => (
                <li key={n.id}>逾期：{n.text}（期望第 {n.expected_chapter} 章回收）</li>
              ))}
            </ul>
          ) : (
            <div className="fs-ok">无逾期伏笔</div>
          )}
        </div>
      )}
    </section>
  );
}
