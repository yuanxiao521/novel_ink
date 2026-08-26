"""世界规则校验器（玄幻硬约束，S4.1）。

把主笔/作者的世界观规则（world_rules_json：结构化 WorldRule）
转成 0 token 的关键词/正则检测，拦截角色行动里的设定硬伤
（如「筑基境修士不能动用空间挪移」→ 角色行动出现"手捏法诀，虚空挪移" → 拦截）。
规则由 LLM 解析一次（cheap），检测永远免费；检测启发式、可调试（对齐 OpenNovel Canon 思路）。
"""
from __future__ import annotations

import re
from typing import Any, Optional

# 否定约束里的禁止词（其后即被禁止的动作短语）
_NEGATION_HINTS = r"不能|无法|禁止|不得|不可以|不准|不允许|无法进行"
# 排他约束里的许可者句式
_EXCLUSIVE_HINTS = r"只能|必须|只有"
# 非许可执行者痕迹（排他规则被违反的常见措辞）
_UNAUTHORIZED_HINTS = ["自己", "擅自", "私自", "偷偷", "独自", "悄悄", "私下"]

_VIOLATION_MODES = ["动用", "施展", "使用", "催动", "释放", "运转", "发动", "施展出", "使出", "祭出", "运转起"]


class RuleViolation:
    __slots__ = ("rule", "detail", "severity")

    def __init__(self, rule: str, detail: str, severity: str = "violation") -> None:
        self.rule = rule
        self.detail = detail
        self.severity = severity


def _forbidden_phrase(constraint: str) -> str:
    """取「不能/无法…」后的被禁短语（限定 2~32 字，去掉句末标点）。"""
    m = re.search(rf"({_NEGATION_HINTS})\s*(.{{2,32}})", constraint or "")
    if not m:
        return ""
    return re.sub(r"[，。、；：！？\s]+$", "", m.group(2))


def _permitted_actor(constraint: str) -> str:
    """取「只能由/必须由 X 才/开启…」里的许可者 X（兼容无尾词的句式）。"""
    if not constraint:
        return ""
    m = re.search(
        rf"({_EXCLUSIVE_HINTS})\s*(?:由|通过)?\s*(.{{1,16}}?)\s*"
        r"(?:才|可|才能|方可|能够|以|开启|打开|进入|执行|触发|进行|启动|激活|使用|动用|继承|获得|接管)",
        constraint,
    )
    if not m:
        m = re.search(rf"({_EXCLUSIVE_HINTS})\s*(?:由|通过)?\s*(.{{1,16}})", constraint)
    return m.group(2).strip() if m else ""


def check_violations(text: str, rules: list[dict]) -> list[RuleViolation]:
    """对一段动作/剧情文本做规则检测，返回违规列表（空=通过）。"""
    if not text or not rules:
        return []
    out: list[RuleViolation] = []
    for r in rules:
        kws = [k for k in (r.get("keywords") or []) if isinstance(k, str) and len(k) >= 2]
        if kws and not any(k in text for k in kws):
            continue  # 主体/概念未在文本出现 → 不触发
        rt = str(r.get("rule_type") or "positive")
        constraint = str(r.get("constraint") or "")
        concept = str(r.get("concept") or constraint[:20])

        if rt == "negation":
            fp = _forbidden_phrase(constraint)
            if not fp:
                continue
            # 命中被禁动作（或其开头子串，容忍解析误差）→ 违规
            if fp in text or (len(fp) >= 5 and fp[:5] in text):
                out.append(RuleViolation(
                    concept, f"规则要求「{constraint}」，但出现被禁行为相关表述", "violation"))
        elif rt == "exclusive":
            actor = _permitted_actor(constraint)
            if not actor:
                continue
            if any(h in text for h in _UNAUTHORIZED_HINTS) and actor not in text:
                out.append(RuleViolation(
                    concept, f"规则要求「{constraint}」，但疑似由非许可者执行", "violation"))
        # conditional / positive：MVP 阶段不拦（suggestion 级，留给成文告警）
    return out


def check_action(action: dict, rules: list[dict]) -> list[RuleViolation]:
    """角色行动护栏：检测行动文本 + 新事实文本（new_fact）。"""
    text = str(action.get("text") or "")
    new_fact = str(action.get("new_fact") or "")
    return check_violations(text, rules) + check_violations(new_fact, rules)


def serialize(vs: list[RuleViolation]) -> list[dict]:
    return [{"rule": v.rule, "detail": v.detail, "severity": v.severity} for v in vs]