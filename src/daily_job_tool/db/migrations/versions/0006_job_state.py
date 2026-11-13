"""job_state: per-job viewed / dismissed markers for the single-owner web UI.

Revision ID: 0006_job_state
Revises: 0005_app_state_saved_searches

Additive only. No new index on jobs: the Verified (last_seen_at), Experience (years_*) and
company filters are residual predicates on top of the existing jobs_my_feed / jobs_feed scans
(measured in tests/web/test_perf.py), and job_state is looked up by its primary key (job_id).
A partial index on dismissed rows keeps the "not dismissed" anti-join and the "Show dismissed"
list cheap.
"""

from alembic import op

revision = "0006_job_state"
down_revision = "0005_app_state_saved_searches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE job_state ("
        " job_id bigint PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,"
        " viewed_at timestamptz,"
        " dismissed_at timestamptz)"
    )
    op.execute(
        "CREATE INDEX job_state_dismissed ON job_state (dismissed_at DESC) "
        "WHERE dismissed_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE job_state")
