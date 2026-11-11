"""Company metadata for the pharma/health registry (industry, staffing flag, exclusion).

Revision ID: 0002_company_meta
Revises: 0001_initial
"""

from alembic import op

revision = "0002_company_meta"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE companies ADD COLUMN industry text, "
        "ADD COLUMN is_staffing boolean NOT NULL DEFAULT false, "
        "ADD COLUMN exclude_reason text"
    )
    op.execute("CREATE INDEX companies_industry ON companies (industry)")


def downgrade() -> None:
    op.execute("DROP INDEX companies_industry")
    op.execute(
        "ALTER TABLE companies DROP COLUMN exclude_reason, "
        "DROP COLUMN is_staffing, DROP COLUMN industry"
    )
