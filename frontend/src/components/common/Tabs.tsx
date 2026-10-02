/* Tabs —— 轻量页签（概览复盘 / 共创面板 / 设定页共用） */

interface Props<T extends string> {
  items: Array<{ key: T; label: string; badge?: number }>;
  value: T;
  onChange: (k: T) => void;
  className?: string;
}

export function Tabs<T extends string>({ items, value, onChange, className }: Props<T>) {
  return (
    <div className={`ws-tabs ${className ?? ''}`}>
      {items.map((it) => (
        <button
          key={it.key}
          className={`ws-tab ${value === it.key ? 'active' : ''}`}
          onClick={() => onChange(it.key)}
        >
          {it.label}
          {typeof it.badge === 'number' && <span className="ws-tab-badge">{it.badge}</span>}
        </button>
      ))}
    </div>
  );
}
