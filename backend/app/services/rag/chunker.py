"""文本切块（v1.49 · 移植自 agent_project/app/ingestion/splitter.py）。

原实现用 LangChain 的 RecursiveCharacterTextSplitter；这里不引 langchain（重依赖、且我们
只需「按中文句读递归切块」这一件事），用 ~60 行等价实现：
  - 分隔符优先级与原实现一致：["\n\n","\n","。","！","？","；","，"," ",""]
  - chunk_size=CHUNK_CHARS / chunk_overlap=CHUNK_OVERLAP（原 600/60，小说素材更短，取 500/60）
  - 产出带 chunk_index，便于稳定 id（重建不抖动）

为什么按句读切而不是定长切：检索片段要能**独立成句**被写手读懂，
把一句话劈成两半的片段在 prompt 里是噪音。
"""
from __future__ import annotations

CHUNK_CHARS = 500
CHUNK_OVERLAP = 60
SEPARATORS = ("\n\n", "\n", "。", "！", "？", "；", "，", " ", "")


def _split_keep(text: str, sep: str) -> list[str]:
    """按分隔符切**并保留分隔符**（句号留在句尾，拼回去还是原句）。"""
    if sep == "":
        return list(text)
    parts = text.split(sep)
    out: list[str] = []
    for i, p in enumerate(parts):
        if i < len(parts) - 1:
            out.append(p + sep)
        elif p:
            out.append(p)
    return out


def _pack(pieces: list[str], size: int) -> list[str]:
    """把句子贪心装进不超过 size 的块。"""
    chunks: list[str] = []
    cur = ""
    for p in pieces:
        if not p:
            continue
        if cur and len(cur) + len(p) > size:
            chunks.append(cur)
            cur = p
        else:
            cur += p
    if cur.strip():
        chunks.append(cur)
    return chunks


def split_text(text: str, size: int = CHUNK_CHARS,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """递归按分隔符切块；返回去空白后的块列表（空文本 → []）。"""
    body = (text or "").strip()
    if not body:
        return []
    pieces: list[str] = [body]
    for sep in SEPARATORS:
        # 还全是超长片段的就继续用更细的分隔符切
        if all(len(p) <= size for p in pieces):
            break
        nxt: list[str] = []
        for p in pieces:
            if len(p) <= size:
                nxt.append(p)
            else:
                nxt.extend(_split_keep(p, sep))
        pieces = nxt
    chunks = _pack(pieces, size)
    if overlap > 0 and len(chunks) > 1:
        # 相邻块尾部重叠：下一块开头补上一块最后 overlap 字，保住跨句语境
        out = [chunks[0]]
        for prev, cur in zip(chunks, chunks[1:]):
            out.append(prev[-overlap:] + cur)
        chunks = out
    return [c.strip() for c in chunks if c.strip()]
