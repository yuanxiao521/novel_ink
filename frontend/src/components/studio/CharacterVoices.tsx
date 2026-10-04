import { useState } from 'react';
import { callAgentTool } from '../../api/novel';

/* ==========================================================================
   CharacterVoices —— 角色在场感（E 批）

   写正文时角色 agent 本来是"不在场"的（写手只拿到角色卡文本）。这里让角色**只读地**说一句：
     · 输入 = 角色卡（腔调/底线）+ 该角色在本文里的 0-token 腔调统计 + 与其相关的正文片段
     · 输出 = 意见卡（情绪 / 心里话 / 哪句不像 / 换成他会怎么说）
   边界（后端强制）：side_effect=read，不改正文、不改账本；**角色 agent 不允许发起任务**。
   ========================================================================== */

interface Opinion {
  char_id: string;
  name: string;
  voice?: string;
  emotion?: string;
  monologue?: string;
  critique?: string;
  line?: string;
  avg_len?: number | null;
  particles?: string[];
  note?: string;
}

interface Props {
  sceneId: string;
  text: string;
  onFlash: (t: string) => void;
}

export function CharacterVoices({ sceneId, text, onFlash }: Props) {
  const [opinions, setOpinions] = useState<Opinion[]>([]);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);

  const ask = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await callAgentTool(sceneId, 'character.voices', { text });
      const data = (res.data ?? {}) as { opinions?: Opinion[]; note?: string };
      setOpinions(data.opinions ?? []);
      setNote(data.note ?? '');
      if ((data.opinions ?? []).length === 0) onFlash(data.note ?? '没有可用的角色意见');
    } catch (e) {
      onFlash(`请角色失败：${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="cv-wrap">
      <div className="cv-head">
        <button className="ws-btn ws-btn-primary" disabled={busy || !text.trim()} onClick={() => void ask()}>
          {busy ? '角色在想…' : '◐ 请角色说一句'}
        </button>
        <span className="s2-hint">只读意见：不改正文、不改账本</span>
      </div>

      {opinions.length === 0 && (
        <div className="s2-hint">
          {note || '点上面的按钮，让本场角色就这段正文说一句 —— 他们会对"哪句不像我"发表意见（不会替你改文）。'}
        </div>
      )}

      {opinions.map((o) => (
        <div className="cv-card" key={o.char_id || o.name}>
          <div className="cv-top">
            <span className="cv-name">{o.name}</span>
            {o.emotion && <span className="cv-emotion">{o.emotion}</span>}
            <span className="cv-metric" title="0-token 腔调统计（来自本文）">
              句长 {o.avg_len ?? '—'}{(o.particles ?? []).length > 0 && ` · ${(o.particles ?? []).join('/')}`}
            </span>
          </div>
          {o.monologue && <div className="cv-mono">「{o.monologue}」</div>}
          {o.critique && <div className="cv-critique">不像我：{o.critique}</div>}
          {o.line && <div className="cv-line">换成我：{o.line}</div>}
          {!o.monologue && o.note && <div className="s2-hint">{o.note}</div>}
        </div>
      ))}
    </div>
  );
}
