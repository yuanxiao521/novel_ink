from pathlib import Path
SRC = Path(r"E:\novel_desk-agent\backend\app\services\agents\editor.py")
lines = SRC.read_text(encoding="utf-8").split(chr(10))
NEW = ["async def perceive_scene(repo, scene_id: str) -> dict:", "    return await perceive(repo, \"scene\", scene_id=scene_id)", "", "def packet_draft(packet: dict) -> str:", "    return draft_text(packet)", ""]
lines[86:199] = NEW
text = chr(10).join(lines)
out = text.split(chr(10))
for i in range(100, 122):
    print(i + 1, "|", out[i] if i < len(out) else "<EOF>")
print("total", len(out))
