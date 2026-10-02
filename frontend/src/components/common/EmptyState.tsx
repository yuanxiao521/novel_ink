import type { ReactNode } from 'react';

/* EmptyState —— 统一空态：图标 + 标题 + 说明 + 主/次动作
   取代各页零散的 12px 灰字提示（"暂无数据"这种不能点、也不告诉用户下一步做什么）。 */

interface Props {
  icon?: string;
  title: string;
  desc?: string;
  primary?: { label: string; onClick: () => void };
  secondary?: { label: string; onClick: () => void };
  children?: ReactNode;
  compact?: boolean;
}

export function EmptyState({ icon = '◌', title, desc, primary, secondary, children, compact }: Props) {
  return (
    <div className={`ws-empty ${compact ? 'compact' : ''}`}>
      <div className="ws-empty-ico">{icon}</div>
      <div className="ws-empty-title">{title}</div>
      {desc && <div className="ws-empty-desc">{desc}</div>}
      {(primary || secondary || children) && (
        <div className="ws-empty-actions">
          {primary && (
            <button className="ws-btn ws-btn-primary" onClick={primary.onClick}>
              {primary.label}
            </button>
          )}
          {secondary && (
            <button className="ws-btn" onClick={secondary.onClick}>
              {secondary.label}
            </button>
          )}
          {children}
        </div>
      )}
    </div>
  );
}
