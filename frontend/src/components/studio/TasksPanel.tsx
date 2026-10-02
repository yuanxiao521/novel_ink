import { useState } from 'react';
import { createAgentTask, patchAgentTask } from '../../api/novel';
import type { AgentTask } from '../../api/novel';

/* ==========================================================================
   TasksPanel —— 任务总线（L3）的界面：请求彩排 / 推进状态

   边界（由后端强制，前端只呈现）：
     · 只有 作者/编排者/主笔/责编 能发起 → 角色 agent 会被 409 拒
     · 状态机 submitted → working → input-required → completed / failed
     · 任务只传"请求与裁决"，正文/账本状态不在这里
   ========================================================================== */

interface Props {
  sceneId: string;
  tasks: AgentTask[];
  onRefresh: () => void;
  onFlash: (t: string) => void;
}

const STATUS_LABEL: Record<string, string> = {
  submitted: '待受理', working: '进行中', 'input-required': '等作者确认', completed: '已完成', failed: '失败',
};
const NEXT: Record<string, Array<{ s: string; label: string }>> = {
  submitted: [{ s: 'working', label: '开始' }, { s: 'input-required', label: '请作者确认' }, { s: 'failed', label: '失败' }],
  working: [{ s: 'input-required', label: '请作者确认' }, { s: 'completed', label: '完成' }, { s: 'failed', label: '失败' }],
  'input-required': [{ s: 'working', label: '继续' }, { s: 'completed', label: '完成' }, { s: 'failed', label: '失败' }],
  completed: [],
  failed: [],
};

export function TasksPanel({ sceneId, tasks, onRefresh, onFlash }: Props) {
  const [busy, setBusy] = useState('');
  const [goal, setGoal] = useState('');

  const create = async (kind: string, text: string) => {
    setBusy('create');
    try {
      await createAgentTask(sceneId, { kind, goal: text, from_agent: 'author' });
      onFlash('已发起任务（角色 agent 不能发起，这条是以「作者」身份）');
      setGoal('');
      onRefresh();
    } catch (e) {
      onFlash(`发起失败：${String(e)}`);
    } finally {
      setBusy('');
    }
  };

  const move = async (id: string, status: string) => {
    setBusy(id);
    try {
      await patchAgentTask(id, status, '');
      onRefresh();
    } catch (e) {
      onFlash(`推进失败：${String(e)}`);
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="tp-wrap">
      <div className="tp-new">
        <input
          className="tp-input"
          placeholder="这场想让角色先演一遍？写一句意图（可空）"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
        />
        <button className="ws-btn ws-btn-primary" disabled={busy === 'create'} onClick={() => void create('rehearsal', goal)}>
          ◐ 请求本场彩排
        </button>
      </div>

      {tasks.length === 0 && (
        <div className="s2-hint">
          还没有任务。任务总线用于跨页动作（如"这场先彩排一次"）：**只传请求与裁决，不传状态**——
          千万字小说的正文、账本永远只在黑板与 PostgreSQL 里。
        </div>
      )}

      {tasks.map((t) => (
        <div className={`tp-task ${t.status}`} key={t.id}>
          <div className="tp-head">
            <span className="tp-kind">{t.kind_label || t.kind}</span>
            <span className="tp-status">{STATUS_LABEL[t.status] ?? t.status}</span>
            <span className="tp-from">{t.from_agent} → {t.to_agent}</span>
          </div>
          {t.goal && <div className="tp-goal">{t.goal}</div>}
          <div className="tp-actions">
            {(NEXT[t.status] ?? []).map((n) => (
              <button key={n.s} className="ws-btn" disabled={busy === t.id} onClick={() => void move(t.id, n.s)}>
                {n.label}
              </button>
            ))}
            {(NEXT[t.status] ?? []).length === 0 && <span className="tp-done">终态</span>}
          </div>
        </div>
      ))}
    </div>
  );
}
