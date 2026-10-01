"""A3 定点重写（spot-fix）：只改"白名单规则命中"的那几句。

三层职责分明（对应《写作优化方案-A3反AI味校验.md》§3.2）：
  · 定位（0-token）：style_checks.sentences_with + rule_policy（含"只提示不改"的规则）
  · 改写（LLM）：只重写被定位的句子，结构化输出走 validate_and_retry
  · 闸门（0-token）：①事实不变（专名/数字/术语/引号内台词逐字）②落地校验（原句必须逐字存在）
    ③双闸复检（**句子级命中数下降** 且 口吻不劣化），任一不满足 → 回退原文

设计与实现要点：
- 复检用"句子级命中数"而非 ai_tone_report 的聚合规则数：聚合阈值是全文级的
  （套话要 ≥3 才报），而 spot-fix 的工作单位是"句"，两者口径必须一致，否则
  单句命中会被判成"命中数未下降"而无谓回退（实现时踩过）。
- 无命中 → 幂等空操作（**不调 LLM**）；LLM 不可用 → 明确提示且不改文。
"""
from __future__ import annotations

import logging
import re

from app.services.engine.schema_retry import validate_and_retry
from app.services.engine.style_checks import (
    CONSERVATIVE_MAX,
    ai_tone_report,
    rule_policy,
    sentences_with,
    voice_prior,
)

logger = logging.getLogger(__name__)

MAX_SENTENCES = 6          # 单次最多改句数（防大改）
VOICE_LEN_TOLERANCE = 0.4  # 角色平均句长允许变化比例（口吻闸门）
NUM_RE = re.compile(r"(?:第|初|头)?[0-9０-９]+(?:\.[0-9]+)?"
                    r"|(?:第|初|头)?[一二三四五六七八九十百千万零两]+"
                    r"(?:天|日|月|年|章|次|里|尺|丈|斤|两|枚|人|招|层|阶|品|星|级|步)")
QUOTE_RE = re.compile(r"[「“\"]([^」”\"]{1,200}?)[」”\"]")

SPOT_FIX_SCHEMA = {
    "rewrites": [{
        "index": "int 句序号（与输入的序号一致）",
        "before": "str 原句（逐字复制，不得改写）",
        "after": "str 改写后的句子",
        "rule": "str 处理的规则（R-A3-1/3/4/5/6）",
        "reason": "str ≤30 字，说明改了什么",
    }],
}

_SPOT_FIX_PROMPT = """你是小说文字编辑。下面列出的句子已由确定性规则判定有「AI 腔」，只改这些句子。
硬约束（违反即作废）：
1. 不得改动任何专名（人物/地名/门派/宝物名）与数字/时间（如 三日、第三日、三步）
2. 不得改动引号内的台词（引号内文字必须逐字保留）
3. 不得改动术语（境界/道具/功法等的标准叫法）
4. 保持角色腔调：不得把话少的人写成话多，不得改变各角色的句长习惯
5. 只处理列出的句子，其余一字不动；每句给一条 rewrite，before 必须逐字复制原句
改写方向：删套话与解释句、抽象情绪换具体物象或动作、拆掉工整同构、去掉冗余虚词。
命中的句子：
{targets}

角色腔调参考：
{characters}

请严格按给定 JSON schema 只返回 rewrites 数组。"""


def _validate_spot_fix(raw) -> str | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("rewrites"), list):
        return "输出必须是含 rewrites 数组的对象"
    for i, r in enumerate(raw.get("rewrites") or []):
        if not isinstance(r, dict):
            return "rewrites[%d] 必须是对象" % i
        if not str(r.get("before") or "").strip():
            return "rewrites[%d] 缺少 before（须逐字给出原句）" % i
        if not str(r.get("after") or "").strip():
            return "rewrites[%d] 缺少非空 after" % i
    return None


def protected_tokens(text: str, characters=None, world_states=None) -> dict:
    """事实闸门输入（四类）：角色名 / 数字时间 / 世界状态术语 / 引号内台词。"""
    names = {str((c or {}).get("name") or "").strip() for c in (characters or [])}
    names = {n for n in names if n}
    terms: set[str] = set()
    for ws in world_states or []:
        kind = str((ws or {}).get("kind") or "")
        if kind in {"term", "numeric"}:
            for key in ("name", "value"):
                v = str((ws or {}).get(key) or "").strip()
                if v:
                    terms.add(v)
    quotes = {m.group(1).strip() for m in QUOTE_RE.finditer(text or "")}
    return {"names": names, "numbers": set(NUM_RE.findall(text or "")),
            "terms": terms, "quotes": {q for q in quotes if q}}


def fact_gate(before: str, after: str, protected: dict) -> str | None:
    """事实不变闸门：通过返回 None；违规则返回违规描述（调用方丢弃该条改写）。

    对称判定：专名/数字的**出现次数必须完全一致**（少一个、多一个都算改动）。
    """
    for n in protected.get("names") or set():
        if before.count(n) != after.count(n):
            return "专名「%s」出现次数被改动" % n
    b_num, a_num = set(NUM_RE.findall(before)), set(NUM_RE.findall(after))
    if b_num != a_num:
        return "数字/时间被改动（%s → %s）" % ("、".join(sorted(b_num)) or "无",
                                              "、".join(sorted(a_num)) or "无")
    for q in protected.get("quotes") or set():
        if q in before and q not in after:
            return "台词「%s…」被改动" % q[:12]
    for t in protected.get("terms") or set():
        if t in before and t not in after:
            return "术语「%s」被改动" % t
    return None


def _voice_not_worse(before_text: str, after_text: str, cast: list[str]) -> tuple[bool, str]:
    """口吻闸门：角色平均句长变化 ≤40%，且不新增语气词种类。"""
    chars = [{"name": n} for n in cast if n]
    if not chars:
        return True, ""
    pa = {m["name"]: m for m in voice_prior(before_text, chars)["per_char"]}
    pb = {m["name"]: m for m in voice_prior(after_text, chars)["per_char"]}
    for name, ma in pa.items():
        mb = pb.get(name) or {}
        la, lb = float(ma.get("avg_len") or 0), float(mb.get("avg_len") or 0)
        if ma.get("dialogues", 0) >= 2 and la > 0 and abs(lb - la) / la > VOICE_LEN_TOLERANCE:
            return False, "角色「%s」平均句长变化 %.0f%%（>%d%%）" % (
                name, 100 * abs(lb - la) / la, int(VOICE_LEN_TOLERANCE * 100))
        added = set(mb.get("particles") or []) - set(ma.get("particles") or [])
        if added:
            return False, "角色「%s」新增语气词「%s」" % (name, "".join(sorted(added)))
    return True, ""


_QUOTE_STRIP = re.compile(r"[「“\x22][^」”\x22]*[」”\x22]")


def _is_dialogue_only(sentence: str) -> bool:
    """整句基本只有引号内台词（去掉引号与标点后剩不到 4 字）→ 不改。

    台词受事实闸门逐字保护，改它必然被回退；直接跳过可省一次无谓的 LLM 往返。
    """
    rest = _QUOTE_STRIP.sub("", sentence or "")
    rest = re.sub(r"[，,、。！？…；;：:\s]", "", rest)
    return len(rest) < 4


def _pick_targets(text: str, cast_names: set[str], max_sentences: int) -> list[dict]:
    """按白名单挑可改写句：跳过 hint-only 规则 / 纯台词句；conservative 规则限句数。"""
    targets: list[dict] = []
    conservative_used = 0
    for hit in sentences_with(text, cast_names):
        if _is_dialogue_only(hit["sentence"]):
            continue
        fixable = [r for r in hit["rules"] if rule_policy(r)["fixable"]]
        if not fixable:
            continue
        modes = {rule_policy(r)["mode"] for r in fixable}
        if modes == {"conservative"}:
            if conservative_used >= CONSERVATIVE_MAX:
                continue
            conservative_used += 1
        targets.append({"index": hit["index"], "sentence": hit["sentence"], "rules": fixable})
        if len(targets) >= max_sentences:
            break
    return targets


async def spot_fix(llm_client, text: str, characters=None, world_states=None,
                   report=None, max_sentences: int = MAX_SENTENCES) -> dict:
    """定点重写主入口。返回值含 after/changes/accepted 与前后报告，便于前端展示与审计。"""
    text = text or ""
    cast = [str((c or {}).get("name") or "").strip() for c in (characters or [])]
    cast = [n for n in cast if n]
    names = set(cast)
    rep_before = report or ai_tone_report(text, exclude=names)
    hits_before = len(sentences_with(text, names))

    if not text.strip():
        return {"after": text, "changes": [], "accepted": True,
                "skipped": "empty-text", "report": rep_before}

    targets = _pick_targets(text, names, max_sentences)
    if not targets:
        return {"after": text, "changes": [], "accepted": True, "skipped": "no-fixable-hit",
                "hits_before": hits_before, "report": rep_before}

    if not getattr(llm_client, "available", False):
        return {"after": text, "changes": [], "accepted": False,
                "error": "LLM 未接入，未做改写", "hits_before": hits_before, "report": rep_before}

    protected = protected_tokens(text, characters, world_states)
    prompt = _SPOT_FIX_PROMPT.format(
        targets="\n".join("%d. [%s] %s" % (t["index"], "/".join(t["rules"]), t["sentence"])
                          for t in targets),
        characters="\n".join("- %s" % n for n in cast) or "（无角色卡）",
    )
    try:
        raw = await validate_and_retry(llm_client, prompt, SPOT_FIX_SCHEMA, _validate_spot_fix)
    except ValueError as e:  # 结构化输出熔断 → 不改文
        return {"after": text, "changes": [], "accepted": False,
                "error": "结构化输出失败：%s" % e, "hits_before": hits_before, "report": rep_before}

    new_text = text
    changes: list[dict] = []
    for r in raw.get("rewrites") or []:
        before = str(r.get("before") or "").strip()
        after = str(r.get("after") or "").strip()
        item = {"index": r.get("index"), "before": before, "after": after,
                "rule": str(r.get("rule") or ""), "reason": str(r.get("reason") or "")}
        if not before or not after or before == after:
            item.update({"applied": False, "blocked_reason": "空改写或前后相同"})
            changes.append(item)
            continue
        if before not in new_text:
            item.update({"applied": False, "blocked_reason": "原句未在正文逐字找到（防错位）"})
            changes.append(item)
            continue
        bad = fact_gate(before, after, protected)
        if bad:
            item.update({"applied": False, "blocked_reason": bad})
            changes.append(item)
            continue
        new_text = new_text.replace(before, after, 1)
        item["applied"] = True
        changes.append(item)

    rep_after = ai_tone_report(new_text, exclude=names)
    hits_after = len(sentences_with(new_text, names))
    voice_ok, voice_detail = _voice_not_worse(text, new_text, cast)

    reasons: list[str] = []
    if new_text == text:
        reasons.append("无有效改写")
    if hits_after >= hits_before:
        reasons.append("句子级命中数未下降（%d → %d）" % (hits_before, hits_after))
    if not voice_ok:
        reasons.append(voice_detail)
    if reasons:
        return {"after": text, "changes": changes, "accepted": False, "reverted": True,
                "reason": "；".join(reasons), "hits_before": hits_before, "hits_after": hits_after,
                "report": rep_before, "report_after": rep_after}
    return {"after": new_text, "changes": changes, "accepted": True,
            "hits_before": hits_before, "hits_after": hits_after,
            "report": rep_before, "report_after": rep_after}
