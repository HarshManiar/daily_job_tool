"""Personal match columns on jobs (ARCHITECTURE.md section 0.2).

Revision ID: 0003_match
Revises: 0002_company_meta
"""

from alembic import op

revision = "0003_match"
down_revision = "0002_company_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE jobs "
        "ADD COLUMN match_tier smallint, "
        "ADD COLUMN match_status text NOT NULL DEFAULT 'include' "
        "CONSTRAINT jobs_match_status_chk CHECK (match_status IN ('include','excluded','flagged')), "
        "ADD COLUMN match_reasons text[] NOT NULL DEFAULT '{}', "
        "ADD COLUMN visa_status text NOT NULL DEFAULT 'not_stated' "
        "CONSTRAINT jobs_visa_status_chk CHECK (visa_status IN ('offered','not_available','not_stated')), "
        "ADD COLUMN visa_evidence text, "
        "ADD COLUMN years_min smallint, "
        "ADD COLUMN years_max smallint, "
        "ADD COLUMN industry_rank smallint NOT NULL DEFAULT 1, "
        "ADD COLUMN location_rank smallint NOT NULL DEFAULT 3, "
        "ADD COLUMN fit_score smallint NOT NULL DEFAULT 0"
    )
    op.execute(
        "CREATE INDEX jobs_my_feed ON jobs "
        "(match_tier NULLS LAST, location_rank, industry_rank, fit_score DESC, freshness_at DESC) "
        "WHERE closed_at IS NULL AND match_status <> 'excluded'"
    )
    op.execute(
        "CREATE INDEX jobs_excluded ON jobs (freshness_at DESC) "
        "WHERE closed_at IS NULL AND match_status = 'excluded'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX jobs_excluded")
    op.execute("DROP INDEX jobs_my_feed")
    op.execute(
        "ALTER TABLE jobs DROP COLUMN fit_score, DROP COLUMN location_rank, "
        "DROP COLUMN industry_rank, DROP COLUMN years_max, DROP COLUMN years_min, "
        "DROP COLUMN visa_evidence, DROP COLUMN visa_status, DROP COLUMN match_reasons, "
        "DROP COLUMN match_status, DROP COLUMN match_tier"
    )
