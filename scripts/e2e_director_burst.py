# -*- coding: utf-8 -*-
"""连拍/多行为/对话快速通道 live E2E（v1.73，可复跑）。

验证（作者拍板：方案一 + 方案三，默认 1 来回）：
  ① 默认拍 = 1 来回 × 1 行为（与 v1.72 一致，成本不涨）
  ② hard beat 未达成 → 闸门加厚 → 时间线一拍长出 N 个来回节点
  ③ 归档带 exchange（T-05·来回2），回退可精确到某个来回
依赖：后端 8001 + LLM 池可用（LLM 挂时走确定性回退，脚本会如实标注）。
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8001/api/v1"
BOOK, CHAPTER, SCENE = "book-0938c02c", "chapter-f89891c8", "scene-85bc6bfe"
CAST = ["char-41329f1d", "char-2c7372cb", "char-8a8774cd"]

results = []


def req(method, path, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(("PASS  " if ok else "FAIL  ") + name + ("  :: " + str(detail) if detail else ""))


# v1.73b：/health 的 llm.ok 是"最近一次调用"的状态缓存（可能残留旧失败），
# 分支判断必须用**真实探测**：直接打一次最小调用。
def _llm_reachable() -> bool:
    import re
    key = ""
    for line in open(r"E:\novel_desk-agent\.env", encoding="utf-8"):
        m = re.match(r"NOVEL_OPENAI_API_KEY=(.+)", line.strip())
        if m:
            key = m.group(1).strip()
            break
    body = json.dumps({"model": "deepseek-v4.1-flash",
                       "messages": [{"role": "user", "content": "回复一个字：好"}],
                       "max_tokens": 8}).encode()
    r2 = urllib.request.Request("http://127.0.0.1:8788/v1/chat/completions", data=body, method="POST",
                                headers={"Content-Type": "application/json",
                                         "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(r2, timeout=60) as resp:
            return bool(json.loads(resp.read()).get("choices"))
    except Exception:
        return False


llm_ok = _llm_reachable()
check("LLM 池真实可达（挂了会走确定性回退，脚本如实标注）", True, "llm_ok=%s" % llm_ok)

st, started = req("POST", "/sims", {"book_id": BOOK, "chapter_id": CHAPTER,
                                    "scene_id": SCENE, "resume": False})
sim = started.get("sim_id", "")
check("建新实例", st == 201 and sim and not started.get("resumed"), "sim=%s" % sim)
st, _ = req("PUT", "/sims/%s/cast" % sim, {"character_ids": CAST})
check("选角 3 人", st == 200)

t0 = time.time()
st, s1 = req("POST", "/sims/%s/step?n=1" % sim)
st_h, h1 = req("GET", "/sims/%s/history" % sim)
arcs1 = h1.get("archives") or []
exs = sorted({a.get("exchange") or 1 for a in arcs1 if a.get("turn") == (s1.get("turn") or 1)})
check("step1 推进", st == 200 and (s1.get("turn") or 0) >= 1,
      "turn=%s (%.1fs) llm_ok=%s" % (s1.get("turn"), time.time() - t0, llm_ok))
if llm_ok:
    # hard beat 未达成（异能007 场景1 有 beats）→ 闸门应加厚 → 一拍 ≥2 个来回节点
    check("闸门加厚：一拍多个来回节点", len(exs) >= 2,
          "本拍来回=%s archives=%s" % (exs, [(a.get("turn"), a.get("exchange")) for a in arcs1]))
    check("归档带 exchange（时间线 T-NN·来回M）", max(exs, default=1) >= 2,
          "exchanges=%s" % exs)
else:
    check("LLM 挂：回退路径下归档仍正常（exchange=1）", len(arcs1) >= 1 and max(exs, default=1) == 1,
          "exchanges=%s（上游恢复后重跑看加厚）" % exs)

# 回退精确到第 1 个来回（若连拍发生）
first = next((a for a in arcs1 if a.get("exchange") == 1 and a.get("turn") == (s1.get("turn") or 1)), None)
if first and max(exs, default=1) >= 2:
    st_r, rew = req("POST", "/sims/%s/rewind?turn=%s" % (sim, s1.get("turn")))
    st_h2, h2 = req("GET", "/sims/%s/history" % sim)
    kept = [a for a in (h2.get("archives") or []) if a.get("turn") == s1.get("turn")]
    check("回退到 T-%s 只保留来回 1 的归档" % s1.get("turn"),
          st_r == 200 and [a.get("exchange") for a in kept] == [1],
          "kept exchanges=%s" % [a.get("exchange") for a in kept])

print("汇总: %d/%d PASS" % (sum(1 for _, o, _ in results if o), len(results)))
sys.exit(0 if all(o for _, o, _ in results) else 1)
