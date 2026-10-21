import { useEffect, useState } from 'react';
import { fetchHealth } from '../../api/novel';
import type { HealthStatus } from '../../api/novel';

/**
 * T9 数据库降级横幅：降级**不再静默**。
 * B18（列漂移）/B19（内存态语义）/B21（反序列化失败）/B22（被空局盖掉）都源于
 * "异常被吞 → 静默落内存态"，这里把它变成界面上看得见的事实（15s 轮询 /health）。
 */
export function DegradedBanner() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = () => {
      fetchHealth()
        .then((r) => {
          if (alive) setHealth(r);
        })
        .catch(() => undefined);
    };
    tick();
    const timer = window.setInterval(tick, 15000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const d = health?.degraded;
  const llm = health?.llm;
  const q = health?.queue;
  const dbBad = Boolean(d?.active);
  // B32：LLM 不可用（模型名错 / 账号异常 / 额度）同样是降级 —— 正文与角色演绎会静默走确定性回退，必须可见
  const llmBad = Boolean(llm && llm.configured && !llm.ok);
  // B51：后台队列没有消费者 —— 排批/审稿会/质量回环会一直排队，前端原来只能等到超时才知道
  const queueBad = Boolean(q && q.alive === false && q.status !== 'unknown');
  /** 状态查询本身失败（Redis 异常）≠ 没有消费者 —— 别误报「请重启 worker」（实测踩过 WRONGTYPE 误报） */
  const queueUnknown = Boolean(q && q.status === 'unknown');
  const queueStuck = Boolean(q && q.alive !== false && (q.stuck ?? 0) > 0);
  if (dismissed || (!dbBad && !llmBad && !queueBad && !queueStuck && !queueUnknown)) return null;
  return (
    <div className="degraded-banner" role="alert">
      <span className="degraded-dot" />
      <span className="degraded-text">
        {dbBad && (
          <>
            数据库降级中：{d?.count} 次操作落内存态
            {(d?.structural ?? 0) > 0 ? `（其中 ${d?.structural} 次疑似 bug）` : ''}
            {d?.last_op ? ` · 最近：${d.last_op}` : ''}
            {d?.last_reason ? ` · ${d.last_reason.slice(0, 80)}` : ''}
          </>
        )}
        {(dbBad || llmBad) && (queueBad || queueStuck || queueUnknown) ? ' ｜ ' : ''}
        {queueUnknown && (
          <>
            队列状态查询失败（Redis 异常，worker 可能仍在运行）：{String(q?.error || '').slice(0, 80)}
            {' '}—— 若排批/审稿会确实一直排队，再重启 worker
          </>
        )}
        {(queueUnknown) && (queueBad || queueStuck) ? ' ｜ ' : ''}
        {queueBad && (
          <>
            后台队列没有消费者（worker 未运行）
            {q?.error ? `：${String(q.error).slice(0, 80)}` : ''}
            {' '}—— 主笔排批 / 审稿会 / 质量回环会一直排队，请重启 worker
          </>
        )}
        {queueStuck && <>后台队列有 {q?.stuck} 个任务长时间卡在 running（疑似僵尸）</>}
        {dbBad && llmBad ? ' ｜ ' : ''}
        {llmBad && (
          <>
            LLM 不可用（{llm?.model}）：{llm?.last_error.slice(0, 100)} · 已失败 {llm?.failures} 次
            —— 正文/角色演绎正在走确定性回退
          </>
        )}
      </span>
      <button className="degraded-close" onClick={() => setDismissed(true)}>
        忽略本次
      </button>
    </div>
  );
}
