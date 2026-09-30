import { useEffect, useState } from 'react';
import { listBookCharacters } from '../../api/novel';
import type { CharacterCardMeta } from '../../api/novel';

/**
 * 选角面板：从该书角色库多选"上场角色"。
 * 由空台引导卡（内联）与角色栏抽屉共用；保存后由调用方写 sim cast。
 */
export function CastPanel({
  bookId,
  currentIds,
  onSave,
  onCancel,
  saving,
  footerHint,
}: {
  bookId: string;
  currentIds: string[];
  onSave: (ids: string[]) => void;
  onCancel?: () => void;
  saving?: boolean;
  footerHint?: string;
}) {
  const [chars, setChars] = useState<CharacterCardMeta[]>([]);
  const [sel, setSel] = useState<string[]>(currentIds);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!bookId) return;
    listBookCharacters(bookId)
      .then((cs) => setChars(cs))
      .catch((e) => setErr(`角色库加载失败：${e instanceof Error ? e.message : e}`));
  }, [bookId]);

  // 外部 currentIds 变化时同步（如切换场景后的 sim 已是不同角色）
  useEffect(() => {
    setSel(currentIds);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentIds.join(',')]);

  const toggle = (id: string) => {
    setSel((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const allIds = chars.map((c) => c.id);
  const allSel = allIds.length > 0 && sel.length === allIds.length;

  return (
    <div className="cast-panel">
      {err ? (
        <div className="char-empty">{err}</div>
      ) : (
        <>
          <div className="cast-toolbar">
            <span className="cast-count">已选 {sel.length} / {chars.length}</span>
            {allIds.length > 0 && (
              <button className="belief-action" onClick={() => setSel(allSel ? [] : allIds)}>
                {allSel ? '全不选' : '全选'}
              </button>
            )}
          </div>
          <div className="cast-list">
            {chars.length === 0 ? (
              <div className="char-empty">该书暂无角色<br />先去「人物」页建卡</div>
            ) : (
              chars.map((c) => (
                <label className={`cast-item ${sel.includes(c.id) ? 'sel' : ''}`} key={c.id}>
                  <input
                    type="checkbox"
                    checked={sel.includes(c.id)}
                    onChange={() => toggle(c.id)}
                  />
                  <span className="cast-name">{c.name}</span>
                </label>
              ))
            )}
          </div>
        </>
      )}
      <div className="cast-foot">
        {footerHint ? <span className="cast-hint">{footerHint}</span> : <span></span>}
        <div className="cast-actions">
          {onCancel && (
            <button className="if-submit" onClick={onCancel} disabled={saving}>取消</button>
          )}
          <button className="if-submit" onClick={() => onSave(sel)} disabled={saving || chars.length === 0}>
            {saving ? '保存中…' : '保存上场角色'}
          </button>
        </div>
      </div>
    </div>
  );
}