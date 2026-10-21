r"""字数口径（唯一真源 · B39）。

背景：同一个「字数」在代码里有 5 种写法，语义还不一样——
  · `service.get_dashboard`   `len(final_prose)`                  ← 含换行/空格
  · `service.export_book_md`  `replace(" ","").replace("\n","")`  ← 只去空格与换行
  · `engine/global_view`      `len(final_prose)`                  ← 含空白
  · `agents/perceive`         `len(final_prose)`                  ← 含空白
  · `agents/material_usage`   `re.sub(r"\s","",prose)`           ← 去所有空白
实测后果：概览 KPI 显示 271 字、导出头部写「全书约 259 字」，同一本书同屏两个数字打架。

口径（定死）：**去掉所有空白字符后的字符数**——中文写作里换行与缩进不算字数。
改口径只改这里，别处一律调用本模块。
"""
from __future__ import annotations

import re

_WS = re.compile(r"\s")


def prose_chars(text: str | None) -> int:
    """一段正文的字数（去掉所有空白字符）。"""
    return len(_WS.sub("", str(text or "")))


def scenes_chars(scenes: list[dict] | None) -> int:
    """一组场景的正文合计（只统计已定稿 `final_prose`，未定稿按 0）。"""
    return sum(prose_chars(s.get("final_prose")) for s in (scenes or []))


def chapter_body(chapter: dict | None, scenes: list[dict] | None = None) -> str:
    """一章的正文：**章正文（chapters.prose_md）优先，空则退回场景聚合**（v1.46 直写链路）。

    与 `service.get_chapter_prose` 的回退规则**必须一致**——否则会出现"显示的字数"和
    "读到的正文"两个口径（B39 的教训：同一本书同屏两个字数）。
    """
    body = str((chapter or {}).get("prose_md") or "").strip()
    if body:
        return body
    parts = [str((s or {}).get("final_prose") or "") for s in (scenes or [])]
    return "\n\n".join(p for p in parts if p.strip())


def chapter_chars(chapter: dict | None, scenes: list[dict] | None = None) -> int:
    """一章的字数（唯一口径）：走 chapter_body 同一份回退逻辑。"""
    return prose_chars(chapter_body(chapter, scenes))
