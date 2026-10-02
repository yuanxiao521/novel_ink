"""B 批补丁（按行区间，纯 Python）：editor.py 的感知/渲染委托统一出口。"""
from pathlib import Path

SRC = Path(r"E:\novel_desk-agent\backend\app\services\agents\editor.py")
lines = SRC.read_text(encoding="utf-8").split(chr(10))

# 边界自检（1-based）：87 perceive_scene / 152 packet_draft / 169 render_editor_prompt / 200 editor_chat
assert lines[86].startswith("async def perceive_scene("), lines[86][:60]
assert lines[151].startswith("def packet_draft("), lines[151][:60]
assert lines[168].startswith("def render_editor_prompt("), lines[168][:60]
assert lines[199].startswith("async def editor_chat("), lines[199][:60]

NEW = '''async def perceive_scene(repo, scene_id: str) -> dict:
    """责编的感知包（B 批）：统一走 perceive(scope="scene")；行为等价，多带**书级约束块**。"""
    return await perceive(repo, "scene", scene_id=scene_id)


def packet_draft(packet: dict) -> str:
    return draft_text(packet)


def packet_summary(packet: dict) -> dict:
    return perceive_summary(packet)


def render_editor_prompt(packet: dict, message: str, text: str) -> str:
    """感知包 → prompt（B 批：统一上下文装配器 persona=editor；工具清单与输出契约仍归责编）。"""
    tool_lines = [
        f"- {t.name}（{t.side_effect}{'·需确认' if t.needs_confirm else ''}）：{t.desc}"
        for t in TOOLS.values()
    ]
    body = render(packet, "editor")
    tail = [
        "【当前正文】" + (text if text.strip() else "（空）"),
        "【作者要求】" + (message.strip() or "（无，按你的判断）"),
        "【可用工具】\\n" + "\\n".join(tool_lines),
        "【输出】只输出 JSON：{\"reply\": \"给作者的中文说明\", \"actions\": [{\"tool\": \"工具名\", \"args\": {...}, \"why\": \"为什么\"}]}。"
        "规则：① 不擅长/无必要时 actions 留空，只解释；② 破坏性工具（保存/改批注状态）**不要**直接调，"
        "在 reply 里请作者在界面确认；③ 一次最多 4 个动作；④ 不要编造工具名；"
        "⑤ **不要预述工具的执行结果**（结果由界面另行展示，你只说打算做什么）。",
    ]
    return body + "\\n\\n" + "\\n\\n".join(tail)
'''.split(chr(10))

lines[86:199] = NEW
text = chr(10).join(lines)

if "from app.services.agents.context import render" not in text:
    text = text.replace(
        "from app.services.agents.executor import ToolDenied, call_tool",
        "from app.services.agents.context import render\n"
        "from app.services.agents.executor import ToolDenied, call_tool\n"
        "from app.services.agents.perceive import draft_text, perceive\n"
        "from app.services.agents.perceive import packet_summary as perceive_summary",
        1,
    )

assert 'return await perceive(repo, "scene", scene_id=scene_id)' in text
assert 'body = render(packet, "editor")' in text
assert "def _scene_packet" not in text          # 旧的本地感知实现必须已移除
compile(text, str(SRC), "exec")                 # 语法自检：不过就不写
SRC.write_text(text, encoding="utf-8")
print("ok lines:", len(text.split(chr(10))))
