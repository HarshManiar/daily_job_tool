"""Range CHECKs on match columns, plus jobs.norm_flags (normalizer attention flags).

Revision ID: 0004_match_checks
Revises: 0003_match

UPGRADE NOTE: this migration does NOT re-score existing rows (and `alembic upgrade` never
runs the matcher). After upgrading, run `firsthand match rescore` once so stored jobs pick up
the new visa / years / employment rules and the new `norm_flags` values.
"""

from alembic import op

revision = "0004_match_checks"
down_revision = "0003_match"
branch_labels = None
depends_on = None

_CHECKS = {
    "jobs_years_min_chk": "years_min IS NULL OR years_min >= 0",
    "jobs_years_max_chk": "years_max IS NULL OR years_max >= 0",
    "jobs_location_rank_chk": "location_rank BETWEEN 0 AND 3",
    "jobs_industry_rank_chk": "industry_rank BETWEEN 0 AND 1",
    "jobs_match_tier_chk": "match_tier IS NULL OR match_tier BETWEEN 1 AND 3",
}


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN norm_flags text[] NOT NULL DEFAULT '{}'")
    for name, expr in _CHECKS.items():
        op.execute(f"ALTER TABLE jobs ADD CONSTRAINT {name} CHECK ({expr})")  # noqa: S608


def downgrade() -> None:
    for name in _CHECKS:
        op.execute(f"ALTER TABLE jobs DROP CONSTRAINT {name}")
    op.execute("ALTER TABLE jobs DROP COLUMN norm_flags")
