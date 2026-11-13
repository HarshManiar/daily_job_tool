"""app_state (key/value) and saved_searches for the single-owner web UI.

Revision ID: 0005_app_state_saved_searches
Revises: 0004_match_checks

No alert fields, no tokens, no users: saved_searches.query is a serialized FeedFilters.
No new indexes on jobs: the new filters are applied as residual predicates on top of the
existing jobs_my_feed / jobs_feed scans (measured in tests/web/test_perf.py).
"""

from alembic import op

revision = "0005_app_state_saved_searches"
down_revision = "0004_match_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE app_state (key text PRIMARY KEY, value text NOT NULL)")
    op.execute(
        "CREATE TABLE saved_searches ("
        " id bigserial PRIMARY KEY,"
        " name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 80),"
        " query jsonb NOT NULL,"
        " created_at timestamptz NOT NULL DEFAULT now())"
    )


def downgrade() -> None:
    op.execute("DROP TABLE saved_searches")
    op.execute("DROP TABLE app_state")
