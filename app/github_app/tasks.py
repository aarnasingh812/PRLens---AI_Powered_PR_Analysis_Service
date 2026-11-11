import logging
import os

import httpx
import requests
from google.genai import errors, types

from app.celery_app import app

from .analysis import Findings, ModelReview, screen, select_comments
from .github import PullRequest
from .store import connect, dispatch_due, get_run, record_usage, reserve_usage

logger = logging.getLogger(__name__)
MAX_DELIVERY_ATTEMPTS = 4


class RetryReview(Exception):
    pass


class PublicationUncertain(RetryReview):
    pass


def retry_delay(error, attempt):
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None) or getattr(error, "code", None)
    headers = response.headers if response is not None else {}
    throttled = status == 429 or (
        status == 403 and ("Retry-After" in headers or headers.get("X-RateLimit-Remaining") == "0")
    )
    if isinstance(error, (RetryReview, httpx.TransportError, requests.ConnectionError, requests.Timeout)):
        return 30 * attempt
    if throttled or (status is not None and status >= 500):
        retry_after = headers.get("Retry-After", "")
        return max(60 * attempt, int(retry_after) if retry_after.isdecimal() else 0)
    return None


def update_run(db, run_id, state, partial):
    db.execute(
        "UPDATE github_review_run SET state = %s, coverage_partial = %s WHERE id = %s",
        (state, partial, run_id),
    )


def save_published(db, run_id, github_review_id):
    db.execute(
        "UPDATE github_review_run SET state = 'published', github_review_id = %s WHERE id = %s",
        (github_review_id, run_id),
    )


def review_marker(run_id):
    return f"<!-- prlens-run:{run_id} -->"


def reconcile_publications(db, pull, target):
    pending = db.execute(
        """
        SELECT id FROM github_review_run
        WHERE (installation_id, repository_id, pr_number) = (%s, %s, %s) AND state = 'publishing'
        """,
        target,
    ).fetchall()
    if pending:
        markers = {review_marker(row["id"]): row["id"] for row in pending}
        for run_id, github_review_id in pull.find_reviews(markers).items():
            save_published(db, run_id, github_review_id)


def repository_allowed(repository_id, private):
    disabled_ids = os.getenv("PRLENS_DISABLED_REPOSITORY_IDS", "").split(",")
    private_ids = os.getenv("PRLENS_GEMINI_PRIVATE_REPOSITORY_IDS", "").split(",")
    return str(repository_id) not in {value.strip() for value in disabled_ids} and (
        not private or str(repository_id) in {value.strip() for value in private_ids}
    )


def review_delivery(db, delivery):
    pull = PullRequest(delivery)
    if not repository_allowed(pull.repository_id, pull.private):
        return "repository_disabled"
    target = delivery["installation_id"], delivery["repository_id"], delivery["pr_number"]
    reconcile_publications(db, pull, target)
    revision = pull.revision()
    if revision is None:
        return "closed"

    run = get_run(db, delivery, revision)
    run_id = run["id"]
    if run["state"] == "publishing":
        raise PublicationUncertain("Awaiting publication reconciliation")
    if run["state"] != "running":
        return run["state"]

    files, partial = pull.files()
    if pull.revision() != revision:
        raise RetryReview("PR changed during diff fetch")
    screening = screen(files, partial)
    if not screening.chunks:
        state = "skipped_unavailable" if screening.partial else "skipped_scope"
        update_run(db, run_id, state, screening.partial)
        return state

    model = ModelReview(screening.chunks)
    reservation = reserve_usage(
        db, run, model.reservation(),
        int(os.getenv("PRLENS_MONTHLY_BUDGET_MICRODOLLARS", "2000000")),
        int(os.getenv("PRLENS_MAX_CALLS_PER_PR", "12")),
    )
    if reservation is None:
        update_run(db, run_id, "skipped_budget", True)
        return "skipped_budget"

    completion = model.request()
    usage = completion.usage_metadata
    if usage is not None and usage.prompt_token_count is not None and usage.total_token_count is not None:
        input_tokens = usage.prompt_token_count
        output_tokens = max(
            usage.total_token_count - input_tokens,
            (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0),
        )
        record_usage(db, reservation, model.cost(input_tokens, output_tokens), input_tokens, output_tokens)
    candidates = completion.candidates
    content = completion.text if candidates and candidates[0].finish_reason == types.FinishReason.STOP else None
    if not content:
        update_run(db, run_id, "failed_model_output", True)
        return "failed_model_output"
    try:
        findings = Findings.model_validate_json(content).findings
    except ValueError:
        update_run(db, run_id, "failed_model_output", True)
        return "failed_model_output"

    previous = db.execute(
        """
        SELECT unnest(finding_fingerprints) AS fingerprint FROM github_review_run
        WHERE (installation_id, repository_id, pr_number) = (%s, %s, %s)
          AND state = 'published'
        """,
        target,
    ).fetchall()
    comments, fingerprints = select_comments(
        findings, screening.anchors, {row["fingerprint"] for row in previous},
    )
    if pull.revision() != revision:
        raise RetryReview("PR changed during analysis")
    if not comments:
        update_run(db, run_id, "quiet", screening.partial)
        return "quiet"

    summary = "PRLens flagged potential bugs in the changed lines below."
    if screening.partial:
        summary += " Review coverage was partial."
    db.execute(
        """
        UPDATE github_review_run SET state = 'publishing', coverage_partial = %s, finding_fingerprints = %s
        WHERE id = %s
        """,
        (screening.partial, fingerprints, run_id),
    )
    try:
        github_review_id = pull.publish(revision, f"{summary}\n\n{review_marker(run_id)}", comments)
    except requests.HTTPError as error:
        if error.response is not None and 400 <= error.response.status_code < 500:
            update_run(db, run_id, "failed_publication", screening.partial)
            return "failed_publication"
        raise PublicationUncertain("GitHub publication outcome is unknown") from error
    save_published(db, run_id, github_review_id)
    return "published"


@app.task(ignore_result=True)
def process_github_delivery(delivery_id):
    with connect() as db:
        delivery = db.execute(
            "SELECT * FROM github_delivery WHERE id = %s AND processed_at IS NULL",
            (delivery_id,),
        ).fetchone()
        if not delivery:
            return
        # The connection owns the lock for the entire review and releases it on exit.
        lock = f"prlens:pr:{delivery['repository_id']}:{delivery['pr_number']}"
        locked = db.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS locked", (lock,),
        ).fetchone()["locked"]
        if not locked:
            return
        delivery = db.execute(
            """
            UPDATE github_delivery SET attempts = attempts + 1
            WHERE id = %s AND processed_at IS NULL RETURNING *
            """,
            (delivery_id,),
        ).fetchone()
        if not delivery:
            return

        attempt = delivery["attempts"]
        try:
            outcome = review_delivery(db, delivery)
        except (RetryReview, requests.RequestException, httpx.TransportError, errors.APIError) as error:
            delay = retry_delay(error, attempt)
            if delay is not None and attempt < MAX_DELIVERY_ATTEMPTS:
                db.execute(
                    "UPDATE github_delivery SET next_dispatch_at = now() + %s * interval '1 second' WHERE id = %s",
                    (delay, delivery_id),
                )
                return
            outcome = f"failed:{type(error).__name__}"
        except (KeyError, ValueError, OSError) as error:
            outcome = f"failed:{type(error).__name__}"
        if outcome.startswith("failed"):
            logger.warning("GitHub delivery %s finished with %s", delivery_id, outcome)
        db.execute(
            "UPDATE github_delivery SET processed_at = now(), outcome = %s WHERE id = %s",
            (outcome, delivery_id),
        )


@app.task(ignore_result=True)
def dispatch_github_deliveries():
    dispatch_due(process_github_delivery.delay)
