import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()


def connect():
    dsn = os.getenv("PRLENS_DATABASE_URL")
    if not dsn:
        raise RuntimeError("PRLENS_DATABASE_URL is required for GitHub App reviews")
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True, connect_timeout=3)


def migrate():
    with connect() as db:
        db.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))


def accept_delivery(delivery_id, installation_id, repository_id, pr_number):
    with connect() as db:
        db.execute(
            """
            INSERT INTO github_delivery (id, installation_id, repository_id, pr_number)
            VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING
            """,
            (delivery_id, installation_id, repository_id, pr_number),
        )


def dispatch_due(enqueue):
    # An unacknowledged delivery becomes eligible again if a broker or worker loses it.
    with connect() as db:
        deliveries = db.execute(
            """
            WITH due AS (
                SELECT id FROM github_delivery
                WHERE processed_at IS NULL AND next_dispatch_at <= now()
                ORDER BY next_dispatch_at LIMIT 25 FOR UPDATE SKIP LOCKED
            )
            UPDATE github_delivery SET next_dispatch_at = now() + interval '90 seconds'
            FROM due WHERE github_delivery.id = due.id RETURNING github_delivery.id
            """
        ).fetchall()
    for delivery in deliveries:
        enqueue(delivery["id"])


def get_run(db, delivery, revision):
    identity = (
        delivery["installation_id"], delivery["repository_id"], delivery["pr_number"],
        revision.base_ref, revision.base_sha, revision.head_sha,
    )
    db.execute(
        """
        INSERT INTO github_review_run
            (installation_id, repository_id, pr_number, base_ref, base_sha, head_sha)
        VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
        """,
        identity,
    )
    return db.execute(
        """
        SELECT * FROM github_review_run WHERE
            (installation_id, repository_id, pr_number, base_ref, base_sha, head_sha)
            = (%s, %s, %s, %s, %s, %s)
        """,
        identity,
    ).fetchone()


def reserve_usage(db, run, estimated_cost, monthly_limit, pr_call_limit):
    with db.transaction():
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"prlens:budget:{run['installation_id']}",),
        )
        month = db.execute(
            "SELECT date_trunc('month', now() AT TIME ZONE 'UTC')::date AS month"
        ).fetchone()["month"]
        spent = db.execute(
            """
            SELECT COALESCE(sum(microdollars), 0) AS total FROM github_model_usage
            WHERE installation_id = %s AND billing_month = %s
            """,
            (run["installation_id"], month),
        ).fetchone()["total"]
        calls = db.execute(
            """
            SELECT count(*) AS pr_calls, count(*) FILTER (WHERE usage.run_id = %s) AS run_calls
            FROM github_model_usage usage JOIN github_review_run run ON run.id = usage.run_id
            WHERE (run.installation_id, run.repository_id, run.pr_number) = (%s, %s, %s)
            """,
            (run["id"], run["installation_id"], run["repository_id"], run["pr_number"]),
        ).fetchone()
        if spent + estimated_cost > monthly_limit or calls["pr_calls"] >= pr_call_limit:
            return None
        if calls["run_calls"] >= 2:
            return None
        return db.execute(
            """
            INSERT INTO github_model_usage (run_id, installation_id, billing_month, microdollars)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (run["id"], run["installation_id"], month, estimated_cost),
        ).fetchone()["id"]


def record_usage(db, reservation_id, cost, input_tokens, output_tokens):
    db.execute(
        """
        UPDATE github_model_usage SET microdollars = %s, input_tokens = %s, output_tokens = %s
        WHERE id = %s
        """,
        (cost, input_tokens, output_tokens, reservation_id),
    )


if __name__ == "__main__":
    migrate()
