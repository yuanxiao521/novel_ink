"""0010: prose_notes + payload_json（阶段⑤ 记账 Agent · 质检明细持久化）

质检员(verifier)的结构化意见（伏笔推进/信念变化/因果）完整存鉴 payload_json，
作者 approve 时据此落账本（foreshadows/beliefs），审计可追溯；
保存正文后自动记账同样写入 payload + 审计记录。

Revision ID: 0010_prose_payload
Revises: 0009_prose_notes
Create Date: 2026-09-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_prose_payload"
down_revision: Union[str, Sequence[str], None] = "0009_prose_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("prose_notes", sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("prose_notes", "payload_json")