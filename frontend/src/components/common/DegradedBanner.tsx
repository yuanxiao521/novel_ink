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
  if (dismissed || !d?.active) return null;
  return (
    <div className="degraded-banner" role="alert">
      <span className="degraded-dot" />
      <span className="degraded-text">
        数据库降级中：{d.count} 次操作落内存态
        {d.structural > 0 ? `（其中 ${d.structural} 次疑似 bug）` : ''}
        {d.last_op ? ` · 最近：${d.last_op}` : ''}
        {d.last_reason ? ` · ${d.last_reason.slice(0, 80)}` : ''}
      </span>
      <button className="degraded-close" onClick={() => setDismissed(true)}>
        忽略本次
      </button>
    </div>
  );
}
