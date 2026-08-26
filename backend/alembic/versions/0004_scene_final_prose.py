"""0004: 场景正文定稿列（正文落库 P0）

- scenes + final_prose（收束后作者手动定稿的整场正文）

Revision ID: 0004_scene_final_prose
Revises: 0003_inspiration_cards
Create Date: 2026-08-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_scene_final_prose"
down_revision: Union[str, Sequence[str], None] = "0003_inspiration_cards"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scenes", sa.Column("final_prose", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("scenes", "final_prose")
