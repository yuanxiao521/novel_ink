"""写手素材筛选（v1.44）——**纯函数、可单测**，不碰库。

背景（三个真 bug，见 Wiki B48/B49/B50）：
- **B48**：`prose_draft` 挑世界状态的过滤条件是
  `kind in {character,item,term} and name in cast_names` —— item/term 的 name 是
  **物品名/术语名**（金属徽章、异常者），永远不可能命中角色名集合 → 这两类**永远注入不进来**。
  实测：正文里两次提到「异常者」、一次提到「金属徽章」，两条都被裁掉。
- **B49**：书级记忆 `chief_perceive` 里塞了一整份**未过滤**的账本，正文 prompt 的 [3] 又注入
  一份过滤后的 → 同一信息给两遍、两套口径（记忆里有「金属徽章/异常者」，[3] 里没有）。
- **B50**：numeric 无条件全放行 → 记账时没解析到对象的**代词脏行**（`name=「他」`）直接进 prompt，
  模型会把「他」当成一个角色名。

这里把「哪些账本行该进本场」变成可测的纯函数，并且**不静默**：被裁的行连同原因一起返回，
调用方可以写进日志/感知包（排查「模型为什么不知道这个设定」时一眼看到）。
"""
from __future__ import annotations

# 代词/占位：记账侧没解析到具体对象，喂给模型只会让它把「他」当成角色名
_PRONOUNS = {
    "他", "她", "它", "我", "你", "您", "他们", "她们", "它们", "咱们", "我们",
    "自己", "对方", "此人", "那人", "这人", "主角", "某人",
}
_PLACEHOLDERS = {"", "-", "—", "无", "未知", "?", "？", "n/a", "na"}


def is_dirty_name(name: object) -> bool:
    """名字是代词/占位/空 → 记账没落到具体对象，不该进 prompt（B50）。"""
    return str(name or "").strip().lower() in (_PRONOUNS | _PLACEHOLDERS)


def _hit(text: str, name: str) -> bool:
    name = str(name or "").strip()
    return bool(name) and name in (text or "")


def select_world_states(
    all_states: list[dict],
    cast_names: set[str] | None = None,
    *,
    scene_text: str = "",
    prose_text: str = "",
) -> tuple[list[dict], list[dict]]:
    """挑选要注入写手的世界状态；返回 `(kept, dropped)`。

    规则（按优先级）：
      1. `edited=True`（作者手改）→ **永远注入**（B27b：作者手改是最高权威，不得被过滤掉）
      2. 名字是代词/占位 → 裁掉（B50）
      3. 值为空 → 裁掉
      4. `kind=character` → name 命中本场角色才注入
      5. `kind=item|term` → name 在**本场设定或正文**里出现过就注入（B48：不再拿物品名去比角色名）
      6. `kind=time|numeric|其它` → 全书级，直接注入

    `dropped` 每项带 reason，调用方据此留痕（不静默丢）。
    """
    cast = {str(x).strip() for x in (cast_names or set()) if str(x).strip()}
    ctx = "\n".join([scene_text or "", prose_text or ""])
    kept: list[dict] = []
    dropped: list[dict] = []
    for s in all_states or []:
        kind = str(s.get("kind") or "")
        name = str(s.get("name") or "")
        value = str(s.get("value") or "")
        row = {"kind": kind, "name": name, "value": value}
        if s.get("edited"):
            kept.append(s)          # 作者手改：最高权威，任何过滤都不能吃掉它
            continue
        if is_dirty_name(name):
            dropped.append({**row, "reason": "名字是代词/占位（记账未落到具体对象）"})
            continue
        if not value.strip():
            dropped.append({**row, "reason": "值为空"})
            continue
        if kind == "character":
            if name in cast:
                kept.append(s)
            else:
                dropped.append({**row, "reason": "该角色本场未上场"})
            continue
        if kind in {"item", "term"}:
            if _hit(ctx, name):
                kept.append(s)
            else:
                dropped.append({**row, "reason": f"本场正文/设定未出现（{kind}）"})
            continue
        kept.append(s)
    return kept, dropped


def drop_note(dropped: list[dict]) -> str:
    """把被裁项压成一行（日志/感知包留痕：为什么模型不知道某个设定）。"""
    if not dropped:
        return ""
    return "；".join(f"{d.get('name')}（{d.get('reason')}）" for d in dropped[:8])

