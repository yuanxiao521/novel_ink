"""0-token 确定性文本校验底座（S2 step5 口吻先验 · A3 反 AI 腔共用）。

设计原则（对应《写作优化方案-S2角色口吻.md》§3.5）：
- **纯函数**：不调 LLM、不读库、无副作用 → 可单测、可预检（成本 0）、可解释；
- **只给信号不下判决**：每条命中的 flag 都带 detail + evidence（正文片段），
  供体检/质检与作者参考，避免用正则替代判断；
- **一份底座两处用**：S2 用 voice_prior()（各角色句长/语气词/称呼/密度），
  A3 用 ai_tone_scan()（套话表/破折号/密度），避免造两套校验器。

跑位：draft 之后、体检之前（service.prose_review）。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 可调阈值
AVG_LEN_LONG = 34.0        # 对话平均句长偏长（"话少"角色尤其可疑）
AVG_LEN_SHORT = 8.0        # 对话平均句长过短（可能是"嗯。""好。"式敷衍）
PARTICLE_OVERLAP = 0.75    # 两角色语气词集合重合率 ≥ 此值 → 疑似同质
PARTICLE_MIN = 3           # 至少各出现 3 种语气词才判同质（样本太小不下结论）
FILLER_PER_100 = 16.0      # 对话里"了/是/的/就/都"密度上限（每百字）
DASH_PER_1000 = 6.0        # 破折号密度上限
ELLIPSIS_PER_1000 = 6.0    # 省略号密度上限
CLICHE_TOTAL = 6           # 套话命中总次数上限（A3 底座，阈值先宽松）
# ---- A3 反 AI 味：新增规则阈值 ----
ELEVATION_MAX_LEN = 26     # R-A3-4 段末"拔高句"长度上限（短句 + 抽象情绪词）
HOMO_MIN = 3               # R-A3-5 连续同构句数下限
HOMO_LEN_DIFF = 2          # R-A3-5 句长差上限（同构判据）
CONTENT_TOP_RATIO = 0.08   # R-A3-6 高频实词占正文比下限
CONTENT_TOP_MIN = 5        # R-A3-6 高频实词出现次数下限

# R-A3-4：段末"升华/总结"词表（抽象情绪、顿悟、收束感）
ELEVATION_WORDS = (
    "明白了", "终于明白", "他知道", "她知道", "这一刻", "终究", "或许", "仿佛一切",
    "原来", "释然", "平静下来", "安静下来", "五味杂陈", "一切都", "从此以后",
)
# R-A3-6：中文近似分词用停用字（含其的 2-gram 视为非实词）
STOPWORDS = set("的了是在和与也就都这那他她它我你们个一不有上下中来往说道着过被把很又"
                "要会能可还而但如若则因此所为以于之其没")

# ---------------------------------------------------------------- 词表
PARTICLES = "啊吧呢吗嘛呀哦嗯哈唉哎咦哼哟啦咯诶喔嗬啧哇咧"
FILLERS = "了是的就都也还很便却"
# 反 AI 腔套话表（A3 底座；命中只报"建议替换/复查"，不做改写）
CLICHES = (
    "深吸一口气", "吸了口气", "眼中闪过一丝", "闪过一丝", "嘴角勾起", "嘴角微微",
    "勾起一抹", "眸光", "眸子", "不禁", "不由自主", "下意识", "仿佛", "似乎",
    "然而", "顿时", "骤然", "旋即", "随即", "莫名", "涌起", "升起", "淡淡",
    "缓缓", "微微", "轻轻", "一抹", "如释重负", "五味杂陈", "心湖", "涟漪",
)
SPEECH_VERBS = "说道问答喊叫笑哼叹嘟囔喃低语喝斥吼"

_SENT_END = "。！？…!?；;"
_QUOTE_RE = re.compile(r"[「“\"]([^」”\"]{1,200}?)[」”\"]")


def _snippet(text: str, start: int, end: int, pad: int = 12) -> str:
    """取命中处上下文片段（用于 evidence，绝不改写原文）。"""
    a = max(0, start - pad)
    b = min(len(text), end + pad)
    return text[a:b].replace("\n", " ")


def split_sentences(text: str) -> list[str]:
    """按中英文句末标点切句（保留引号内容完整，不切分引号内部）。"""
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text or "":
        if ch in "「“\"":
            depth += 1
        elif ch in "」”\"":
            depth = max(0, depth - 1)
        buf.append(ch)
        if depth == 0 and ch in _SENT_END:
            s = "".join(buf).strip()
            if s:
                out.append(s)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def extract_dialogues(text: str, cast_names: list[str]) -> list[dict]:
    """抽取引号台词并做**演讲人归属**（0-token 启发式，归类不了就留 None）。

    归属优先级：①引号前紧邻「名：」；②引号前 24 字内「名+说/道/问…」；
    ③引号后 14 字内「名+说/道…」；③b**引号后紧跟角色名**（无言语动词也算：中文常见
    中置归属「…」陈默没回头，「…」，两个引号同属该角色）；④本引号之前、同句内唯一
    角色名；⑤**紧邻上一个引号**（中间只有标点空白）→ 继承上一说话人。
    都失败 → speaker=None（宁可留空，不硬猜；代词「他/她」不做猜测）。
    """
    text = text or ""
    names = [n for n in (cast_names or []) if n]
    out: list[dict] = []
    last_speaker: str | None = None
    prev_end = 0  # 上一个引号的结束位置：用它限定归属窗口，避免"串台"
    matches = list(_QUOTE_RE.finditer(text))
    for idx, m in enumerate(matches):
        content = m.group(1)
        nxt = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        # 关键：before 只看"上一个引号之后 → 本引号之前"的归属段，after 不越过下一个引号，
        # 否则会把上一句/下一句的说话人算到这一句头上（实测踩过两种串台）。
        before = text[max(prev_end, m.start() - 24):m.start()]
        after = text[m.end():min(m.end() + 14, nxt)]
        speaker = None
        for n in names:  # ① 名：紧邻
            if re.search(rf"{re.escape(n)}[：:]\s*$", before):
                speaker = n
                break
        if speaker is None:  # ② 名前 + 言语动词
            for n in names:
                if re.search(rf"{re.escape(n)}[^。！？…\n]{{0,8}}[{SPEECH_VERBS}]", before):
                    speaker = n
                    break
        if speaker is None:  # ③ 名后 + 言语动词（紧随引号）
            for n in names:
                if re.search(rf"^[，,、]?\s*{re.escape(n)}[^。！？…\n]{{0,6}}[{SPEECH_VERBS}]", after):
                    speaker = n
                    break
        if speaker is None:  # ③b 中置归属：「…」名+动作，「…」（区间内无「：」才算）
            seg = text[m.end():nxt]
            if "：" not in seg and ":" not in seg:
                for n in names:
                    if re.match(rf"^[，,、]?\s*{re.escape(n)}", seg):
                        speaker = n
                        break
        if speaker is None:  # ④ 本引号之前、同句内唯一角色名（同样限定窗口）
            sent_start = max((text.rfind(c, 0, m.start()) for c in _SENT_END), default=-1) + 1
            window = text[max(sent_start, prev_end, m.start() - 40):m.start()]
            hit = [n for n in names if n in window]
            if len(hit) == 1:
                speaker = hit[0]
        if speaker is None and last_speaker:  # ⑤ 紧邻上一引号 → 继承（连续引号同人）
            between = text[prev_end:m.start()]
            if re.fullmatch(r"[，,、。！？…；;：:\s「」“”'’\x22]*", between or ""):
                speaker = last_speaker
        out.append({"speaker": speaker, "text": content, "span": (m.start(), m.end()),
                    "evidence": _snippet(text, m.start(), m.end(), 0)})
        if speaker:
            last_speaker = speaker
        prev_end = m.end()
    return out


def per_character_metrics(text: str, characters: list[dict] | None) -> list[dict]:
    """各角色台词统计：句数 / 平均句长 / 语气词集合 / 填充词密度。"""
    names = [str((c or {}).get("name") or "").strip() for c in (characters or [])]
    names = [n for n in names if n]
    dialogues = extract_dialogues(text, names)
    rows: list[dict] = []
    for n in names:
        lines = [d["text"] for d in dialogues if d["speaker"] == n]
        body = "".join(lines)
        lengths = [len(s) for s in split_sentences(body)] or ([len(body)] if body else [])
        particles = sorted({ch for ch in body if ch in PARTICLES})
        fillers = {ch: body.count(ch) for ch in FILLERS if body.count(ch)}
        rows.append({
            "name": n,
            "dialogues": len(lines),
            "avg_len": round(sum(lengths) / len(lengths), 1) if lengths else 0.0,
            "particles": particles,
            "filler_per_100": round(sum(fillers.values()) * 100 / len(body), 1) if body else 0.0,
        })
    return rows


def homogeneity(metrics: list[dict]) -> dict:
    """语气词集合重合率（Jaccard）→ 超阈值判"疑似同质"。"""
    pairs: list[dict] = []
    flagged: list[dict] = []
    rows = [m for m in metrics if len(m.get("particles") or []) >= PARTICLE_MIN]
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = set(rows[i]["particles"]), set(rows[j]["particles"])
            if not a or not b:
                continue
            jac = len(a & b) / len(a | b)
            pairs.append({"a": rows[i]["name"], "b": rows[j]["name"], "overlap": round(jac, 2)})
            if jac >= PARTICLE_OVERLAP:
                flagged.append({
                    "kind": "homogeneity",
                    "char": rows[i]["name"] + "/" + rows[j]["name"],
                    "detail": f"语气词集合重合 {jac:.0%}（{''.join(sorted(a & b))}）→ 疑似同质",
                    "evidence": "",
                })
    return {"pairs": pairs, "flagged": flagged}


def _address_variants(name: str) -> list[str]:
    """由角色名推候选称呼（林尘 → 林兄/林哥/老林/小林/尘儿/阿尘…）。"""
    if len(name) < 2:
        return []
    surname, given = name[0], name[1:]
    out = [f"{surname}兄", f"{surname}哥", f"{surname}姐", f"{surname}老", f"老{surname}",
           f"小{surname}", f"阿{given}", f"{given}儿", f"{given}哥", f"{given}姐"]
    return [t for t in out if t != name]


def address_terms(text: str, characters: list[dict] | None) -> dict:
    """称呼表：正文里每个角色出现了哪些称呼变体；≥2 种 → 标"待确认"。

    0-token 无法真正验证"卡内关系"（角色卡无关系表），因此：
    - 变体出现在**卡内文本**（summary/voice/traits/…）里 → 视为已认可（sanctioned）；
    - 其余变体单独列出供作者确认（不判违规）。
    """
    text = text or ""
    terms: dict[str, list[str]] = {}
    findings: list[dict] = []
    for c in characters or []:
        name = str((c or {}).get("name") or "").strip()
        if not name:
            continue
        card_text = " ".join(str((c or {}).get(k) or "") for k in
                             ("summary", "voice", "traits", "core_beliefs", "bottom_lines"))
        found = [t for t in _address_variants(name) if t in text]
        if found:
            terms[name] = found
        if len(found) >= 2:
            sanctioned = [t for t in found if t in card_text]
            unsanctioned = [t for t in found if t not in sanctioned]
            if len(unsanctioned) >= 2 or (sanctioned and unsanctioned):
                findings.append({
                    "kind": "address",
                    "char": name,
                    "detail": f"正文出现 {len(found)} 种称呼（{'、'.join(found)}）"
                              + (f"；卡内认可：{'、'.join(sanctioned)}" if sanctioned else "；卡内未提"),
                    "evidence": unsanctioned or found,
                })
    return {"terms": terms, "findings": findings}


def ai_tone_scan(text: str) -> dict:
    """A3 底座：反 AI 腔确定性信号（套话表 / 破折号 / 省略号 / 填充词密度）。"""
    text = text or ""
    n = max(1, len(text))
    cliches: list[dict] = []
    for term in CLICHES:
        cnt = text.count(term)
        if cnt:
            idx = text.find(term)
            cliches.append({"term": term, "count": cnt,
                            "evidence": _snippet(text, idx, idx + len(term), 10)})
    cliches.sort(key=lambda x: -x["count"])
    dash = text.count("——") + text.count("—")
    ellipsis = text.count("……") + text.count("...")
    fillers = sum(text.count(ch) for ch in FILLERS)
    flags: list[dict] = []
    total_cliche = sum(c["count"] for c in cliches)
    if total_cliche >= CLICHE_TOTAL:
        flags.append({"kind": "cliche", "char": "", "evidence": "",
                      "detail": f"套话命中 {total_cliche} 次：" +
                                "、".join(f"{c['term']}×{c['count']}" for c in cliches[:5])})
    if dash * 1000 / n > DASH_PER_1000:
        flags.append({"kind": "dash", "char": "", "evidence": "",
                      "detail": f"破折号密度 {dash * 1000 / n:.1f}/千字（阈值 {DASH_PER_1000}）"})
    if ellipsis * 1000 / n > ELLIPSIS_PER_1000:
        flags.append({"kind": "ellipsis", "char": "", "evidence": "",
                      "detail": f"省略号密度 {ellipsis * 1000 / n:.1f}/千字（阈值 {ELLIPSIS_PER_1000}）"})
    return {"cliches": cliches, "dash": dash, "ellipsis": ellipsis,
            "filler_per_100": round(fillers * 100 / n, 1), "flags": flags}


# ---------------------------------------------------------------- A3 反 AI 味（R-A3-1~6）
def paragraphs(text: str) -> list[str]:
    """按空行/换行切段（段末句判定需要"段"的边界）。"""
    return [p.strip() for p in re.split(r"\n\s*\n|\n", text or "") if p.strip()]


def elevation_endings(text: str) -> list[dict]:
    """R-A3-4：段末句命中"升华/总结"词表且是短句 → 疑似每段结尾拔高。"""
    out: list[dict] = []
    for p in paragraphs(text):
        sents = split_sentences(p)
        if not sents:
            continue
        last = sents[-1].strip("「」“”\"' \t")
        hit = next((w for w in ELEVATION_WORDS if w in last), None)
        if hit and len(last) <= ELEVATION_MAX_LEN:
            idx = text.find(last)
            idx = idx if idx >= 0 else 0
            out.append({"rule": "R-A3-4", "severity": "warning",
                        "detail": "段末疑似拔高：「%s」收尾且句长仅 %d 字（阈值 ≤%d）"
                                  % (hit, len(last), ELEVATION_MAX_LEN),
                        "evidence": _snippet(text, idx, idx + len(last), 6)})
    return out


def homogeneous_runs(text: str) -> list[dict]:
    """R-A3-5：连续 ≥3 句"同构"（**首字相同** 且句长差 ≤2）→ 工整排比式假流畅。

    要求首字相同是为了精度：AI 腔典型形态是"他…。他…。他…。"这种同主语排比，
    单纯句长接近不算（否则会把正常叙述误伤）。
    """
    sents = [s.strip() for s in split_sentences(text or "") if len(s.strip()) >= 4]
    out: list[dict] = []
    i = 0
    while i < len(sents):
        j = i + 1
        while (j < len(sents) and sents[j][0] == sents[j - 1][0]
               and abs(len(sents[j]) - len(sents[j - 1])) <= HOMO_LEN_DIFF):
            j += 1
        if j - i >= HOMO_MIN:
            run = sents[i:j]
            out.append({"rule": "R-A3-5", "severity": "warning",
                        "detail": "连续 %d 句同构（同首字「%s」+ 句长差 ≤%d）→ 排比式工整"
                                  % (len(run), run[0][0], HOMO_LEN_DIFF),
                        "evidence": "".join(run)[:80]})
        i = j
    return out


def top_content_words(text: str) -> list[dict]:
    """R-A3-6：高频实词集中（2-gram 近似分词）→ 词穷式复读。

    判据：出现 ≥5 次 且占正文汉字数 ≥8%；命中即 violation（比"套话"更伤文本）。
    """
    chars = [c for c in (text or "") if "\u4e00" <= c <= "\u9fff"]
    total = len(chars) or 1
    grams: dict[str, int] = {}
    for i in range(len(chars) - 1):
        g = chars[i] + chars[i + 1]
        if any(ch in STOPWORDS for ch in g):
            continue
        grams[g] = grams.get(g, 0) + 1
    out: list[dict] = []
    for g, n in sorted(grams.items(), key=lambda kv: -kv[1]):
        if n >= CONTENT_TOP_MIN and n / total >= CONTENT_TOP_RATIO:
            out.append({"rule": "R-A3-6", "severity": "violation",
                        "detail": "实词「%s」出现 %d 次（占正文 %.0f%%，阈值 %d 次 / %.0f%%）→ 复读"
                                  % (g, n, 100 * n / total, CONTENT_TOP_MIN, 100 * CONTENT_TOP_RATIO),
                        "evidence": g})
        if len(out) >= 3:
            break
    return out


def ai_tone_report(text: str) -> dict:
    """A3 反 AI 味报告：既有信号（R-A3-1 套话 / R-A3-2 标点 / R-A3-3 填充词）
    + 新增 R-A3-4 段末拔高 / R-A3-5 同构排比 / R-A3-6 高频实词，统一带 rule/severity/evidence。

    与 ai_tone_scan（S2 先验用）**并存不回改**：scan 给"信号"，report 给"可拦截的规则清单"。
    """
    tone = ai_tone_scan(text)
    rules: list[dict] = []
    for f in tone.get("flags") or []:
        kind = f.get("kind")
        rid = {"cliche": "R-A3-1", "dash": "R-A3-2", "ellipsis": "R-A3-2"}.get(kind)
        if rid:
            rules.append({"rule": rid, "severity": "warning",
                          "detail": str(f.get("detail") or ""), "evidence": ""})
    # R-A3-3 填充词密度：ai_tone_scan 只把它当"指标"返回（未进 flags），
    # 这里按同一阈值升级成规则 —— 否则规则表里有一条永远不可能命中（实测踩过）。
    filler = float(tone.get("filler_per_100") or 0)
    if filler > FILLER_PER_100:
        rules.append({"rule": "R-A3-3", "severity": "warning",
                      "detail": "填充词（了/是/的/就/都…）密度 %.1f/百字（阈值 %.1f）→ 语感拖沓"
                                % (filler, FILLER_PER_100),
                      "evidence": ""})
    rules += elevation_endings(text) + homogeneous_runs(text) + top_content_words(text)
    by_rule: dict[str, int] = {}
    for r in rules:
        by_rule[r["rule"]] = by_rule.get(r["rule"], 0) + 1
    return {
        "rules": rules,
        "counts": {"total": len(rules),
                   "violation": len([r for r in rules if r["severity"] == "violation"]),
                   "by_rule": by_rule},
        "cliches": tone.get("cliches") or [],
        "metrics": {"dash": tone.get("dash"), "ellipsis": tone.get("ellipsis"),
                    "filler_per_100": tone.get("filler_per_100"), "chars": len(text or "")},
        "clean": not rules,
    }


def voice_prior(text: str, characters: list[dict] | None = None) -> dict:
    """口吻先验总入口：各角色统计 + 同质判定 + 称呼表 + 反 AI 腔信号 + 汇总 flags。"""
    metrics = per_character_metrics(text, characters)
    flags: list[dict] = []
    for m in metrics:
        if m["dialogues"] and m["avg_len"] >= AVG_LEN_LONG:
            flags.append({"kind": "avg_len", "char": m["name"], "evidence": "",
                          "detail": f"台词平均 {m['avg_len']} 字/句（偏长，阈值 {AVG_LEN_LONG}）"})
        if m["dialogues"] >= 3 and m["avg_len"] <= AVG_LEN_SHORT:
            flags.append({"kind": "avg_len", "char": m["name"], "evidence": "",
                          "detail": f"台词平均 {m['avg_len']} 字/句（过短/敷衍，阈值 {AVG_LEN_SHORT}）"})
        if m["filler_per_100"] > FILLER_PER_100:
            flags.append({"kind": "filler", "char": m["name"], "evidence": "",
                          "detail": f"台词填充词密度 {m['filler_per_100']}/百字（阈值 {FILLER_PER_100}）"})
    homo = homogeneity(metrics)
    addr = address_terms(text, characters)
    tone = ai_tone_scan(text)
    flags += homo["flagged"] + addr["findings"] + tone["flags"]
    # 未归属台词计数：代词/无归属写法（如"他低声道：「…」"）诚实标注，供人工复核
    names = [str((c or {}).get("name") or "").strip() for c in (characters or [])]
    unresolved = len([d for d in extract_dialogues(text, [n for n in names if n])
                      if not d["speaker"]])
    if unresolved:
        flags.append({"kind": "unresolved", "char": "", "evidence": "",
                      "detail": "%d 句台词无法归属（代词/无归属写法）→ 分账只按已归属部分计" % unresolved})
    return {"per_char": metrics, "homogeneity": homo, "address": addr,
            "ai_tone": tone, "unresolved": unresolved, "flags": flags}


def prior_block(prior: dict, limit: int = 6) -> str:
    """把先验压成 prompt 可用的紧凑文本块（体检时作为"确定性事实"参考）。"""
    if not prior:
        return "（无）"
    lines: list[str] = []
    for m in prior.get("per_char") or []:
        if not m.get("dialogues"):
            continue
        lines.append(f"- {m['name']}：{m['dialogues']} 句台词，平均 {m['avg_len']} 字/句，"
                     f"语气词「{''.join(m.get('particles') or []) or '无'}」")
    if not lines:
        lines.append("- （正文未识别到可归属的台词）")
    if prior.get("unresolved"):
        lines.append("- 另有 %d 句无法归属（代词/无归属写法），分账仅按已归属部分" % prior["unresolved"])
    flags = prior.get("flags") or []
    if flags:
        lines.append("- 确定性信号：" + "；".join(str(f.get("detail") or "") for f in flags[:limit]))
    return "\n".join(lines)
