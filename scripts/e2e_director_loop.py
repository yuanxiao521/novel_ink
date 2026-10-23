# -*- coding: utf-8 -*-
"""导演台闭环 E2E（v1.72 实测脚本）：选角 → 推演 → checkpoint → 回退 → 重演 → 定稿 → 清理。

全是真 HTTP + 真 LLM 调用（不 mock），失败也如实打印。跑完输出 PASS/FAIL 汇总。
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8001/api/v1"
BOOK = "book-0938c02c"
CHAPTER = "chapter-f89891c8"
SCENE = "scene-85bc6bfe"
CAST = ["char-41329f1d", "char-2c7372cb", "char-8a8774cd"]  # 主角 / 关键配角 / 反派
LEGACY_SIM = "sim-d58495ab"

results = []


def req(method, path, body=None, timeout=240):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except Exception:
            return e.code, {"raw": raw[:300].decode("utf-8", "replace")}


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(("PASS  " if ok else "FAIL  ") + name + ("  :: " + str(detail) if detail else ""))
    return ok


def head(t):
    print("\n---- " + t + " ----")


head("0. 夹具")
st, scene0 = req("GET", "/scenes/" + SCENE)
old_prose = (scene0.get("final_prose") or "")
check("读取场景（异能007·场景1）", st == 200, "旧 final_prose %d 字（测试后会还原）" % len(old_prose))

head("1. 建 sim（从头新开）")
t0 = time.time()
st, started = req("POST", "/sims", {"book_id": BOOK, "chapter_id": CHAPTER,
                                    "scene_id": SCENE, "resume": False})
sim = started.get("sim_id", "")
check("POST /sims 新建实例", st == 201 and sim and not started.get("resumed"),
      "sim=%s resumed=%s (%.1fs)" % (sim, started.get("resumed"), time.time() - t0))

head("2. 选角（主角 + 关键配角 + 反派）")
st, casted = req("PUT", "/sims/%s/cast" % sim, {"character_ids": CAST})
chars = casted.get("characters") or []
check("PUT /sims/{id}/cast 3 人上场", st == 200 and len(chars) == 3,
      "characters=%s" % chars)

head("3. 推演第 1 回合")
t0 = time.time()
st, s1 = req("POST", "/sims/%s/step?n=1" % sim)
st_h, h1 = req("GET", "/sims/%s/history" % sim)
arcs1 = h1.get("archives") or []
a1 = arcs1[-1] if arcs1 else {}
check("step1 推进到 T-1", st == 200 and s1.get("turn") == 1,
      "turn=%s (%.1fs)" % (s1.get("turn"), time.time() - t0))
check("step1 归档有内容（角色行动/旁白）",
      len(a1.get("events") or []) > 0 or len((a1.get("prose") or "")) > 0,
      "events=%d prose=%d字 summary=%s" % (len(a1.get("events") or []),
                                           len(a1.get("prose") or ""),
                                           (a1.get("summary") or "")[:16]))

head("4. 再推演 2 回合（时间线累积）")
t0 = time.time()
st, s3 = req("POST", "/sims/%s/step?n=2" % sim)
st_h, h3 = req("GET", "/sims/%s/history" % sim)
arcs3 = h3.get("archives") or []
check("step n=2 推进（turn 前进）", st == 200 and (s3.get("turn") or 0) >= 2,
      "turn=%s (%.1fs)" % (s3.get("turn"), time.time() - t0))
check("时间线累积归档", len(arcs3) >= 2,
      "archives=%d turns=%s total_archived=%s filtered_empty=%s"
      % (len(arcs3), [a.get("turn") for a in arcs3],
         h3.get("total_archived"), h3.get("filtered_empty")))
check("归档里没有空壳（total == 有效条数）",
      h3.get("total_archived") == len(arcs3) and h3.get("filtered_empty") == 0,
      "total=%s valid=%s" % (h3.get("total_archived"), len(arcs3)))

head("5. checkpoint 快照可读（任意回合）")
if arcs3:
    one = arcs3[0]
    check("回合归档含 张力/类型/摘要/成文/思考",
          all(k in one for k in ("turn", "tension", "cls", "summary", "prose", "events", "thoughts")),
          "T-%s tension=%s cls=%s prose=%d字 thoughts=%d"
          % (one.get("turn"), one.get("tension"), one.get("cls"),
             len(one.get("prose") or ""), len(one.get("thoughts") or [])))
else:
    check("回合归档字段", False, "无归档")

head("6. 回退到 T-1（截断 + 可重演）")
target = arcs3[0].get("turn") if arcs3 else 1
st, rew = req("POST", "/sims/%s/rewind?turn=%s" % (sim, target))
st_h, h_after = req("GET", "/sims/%s/history" % sim)
arcs_after = h_after.get("archives") or []
check("rewind 返回目标回合", st == 200 and rew.get("turn") == target,
      "turn=%s" % rew.get("turn"))
check("rewind 截断归档（只剩 <= T-%s）" % target,
      all((a.get("turn") or 0) <= target for a in arcs_after) and len(arcs_after) == target,
      "archives=%s" % [a.get("turn") for a in arcs_after])
st, rew2 = req("POST", "/sims/%s/rewind?turn=999" % sim)
check("rewind 到不存在的回合要报错（不许静默成功）", st >= 400, "status=%s" % st)

head("7. 重演（回退后继续推进）")
t0 = time.time()
st, s_re = req("POST", "/sims/%s/step?n=1" % sim)
st_h, h_re = req("GET", "/sims/%s/history" % sim)
arcs_re = h_re.get("archives") or []
check("回退后可继续演（turn 前进）", st == 200 and (s_re.get("turn") or 0) == target + 1,
      "turn=%s (%.1fs)" % (s_re.get("turn"), time.time() - t0))
check("重演后归档增加", len(arcs_re) == target + 1,
      "archives=%s" % [a.get("turn") for a in arcs_re])

head("8. 定稿（聚合成文 → scenes.final_prose）")
st, fin = req("POST", "/sims/%s/finalize" % sim)
st_s, scene_after = req("GET", "/scenes/" + SCENE)
new_prose = scene_after.get("final_prose") or ""
check("finalize 写出场景定稿正文", st == 200 and (fin.get("word_count") or 0) > 0 and new_prose,
      "word_count=%s final_prose=%d字" % (fin.get("word_count"), len(new_prose)))
# 还原测试前的场景正文（不污染作者数据）
st_r, _ = req("PUT", "/scenes/%s/prose" % SCENE, {"text": old_prose})
st_s2, scene_restored = req("GET", "/scenes/" + SCENE)
check("测试后还原场景正文", st_r == 200 and (scene_restored.get("final_prose") or "") == old_prose,
      "restored=%d字" % len(scene_restored.get("final_prose") or ""))

head("9. 存量空壳清理（sim-d58495ab：19 条假回合）")
st_b, h_before = req("GET", "/sims/%s/history" % LEGACY_SIM)
st_p, pruned = req("POST", "/sims/%s/prune-archives" % LEGACY_SIM)
st_h, h_legacy = req("GET", "/sims/%s/history" % LEGACY_SIM)
before_empty = h_before.get("filtered_empty") or 0
# 该 sim 的 19 条空壳在 v1.72 首跑时已清掉（幂等），复跑时 filtered_empty=0 属正常
check("清理前存量空壳数为 0 或 19（幂等场景）", before_empty in (0, 19),
      "total_archived=%s filtered_empty=%s"
      % (h_before.get("total_archived"), before_empty))
check("prune 物理删除空壳（删掉的条数 == 清理前的空壳数）",
      st_p == 200 and (pruned.get("removed") or 0) == before_empty
      and (pruned.get("kept") or 0) == len(h_before.get("archives") or []),
      "removed=%s kept=%s（复跑时 removed=0 说明已干净）"
      % (pruned.get("removed"), pruned.get("kept")))
check("清理后 /history 只剩真实回合（无空壳）",
      h_legacy.get("total_archived") == len(h_legacy.get("archives") or [])
      and h_legacy.get("filtered_empty") == 0
      and len(h_legacy.get("archives") or []) >= 1,
      "total_archived=%s filtered_empty=%s archives=%s"
      % (h_legacy.get("total_archived"), h_legacy.get("filtered_empty"),
         [a.get("turn") for a in (h_legacy.get("archives") or [])]))
st_p2, pruned2 = req("POST", "/sims/%s/prune-archives" % LEGACY_SIM)
check("prune 幂等（再点一次 removed=0 且不删真实回合）",
      st_p2 == 200 and (pruned2.get("removed") or 0) == 0
      and (pruned2.get("kept") or 0) == len(h_legacy.get("archives") or []),
      "removed=%s kept=%s" % (pruned2.get("removed"), pruned2.get("kept")))

head("汇总")
ok = sum(1 for _, o, _ in results if o)
print("%d/%d PASS" % (ok, len(results)))
for n, o, d in results:
    if not o:
        print("  FAILED:", n, "::", d)
sys.exit(0 if ok == len(results) else 1)
