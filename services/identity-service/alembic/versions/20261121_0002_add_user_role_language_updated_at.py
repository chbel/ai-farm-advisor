"""add role, preferred_language and updated_at to users

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Server defaults fill existing rows, so the columns can be NOT NULL immediately.
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=20), server_default="farmer", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column(
            "preferred_language", sa.String(length=8), server_default="fr", nullable=False
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('farmer', 'technician', 'consultant', 'admin')",
    )
    op.create_check_constraint(
        "ck_users_preferred_language",
        "users",
        "preferred_language IN ('fr', 'en', 'aeb')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_preferred_language", "users", type_="check")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "updated_at")
    op.drop_column("users", "preferred_language")
    op.drop_column("users", "role")
