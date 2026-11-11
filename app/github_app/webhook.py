import hashlib
import hmac
import json
import os
from typing import Annotated

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .store import accept_delivery

router = APIRouter()
MAX_WEBHOOK_BYTES = 2 * 1024 * 1024


class GitHubID(BaseModel):
    id: Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]


class PullRequestDelivery(BaseModel):
    installation: GitHubID
    repository: GitHubID
    number: Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)]


def parse_delivery(secret, signature, event, delivery_id, body):
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        raise HTTPException(401, "Invalid webhook signature")
    if event != "pull_request":
        return None
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("expected an object")
        action = payload.get("action")
        if action == "edited":
            changes = payload.get("changes")
            if not isinstance(changes, dict) or "base" not in changes:
                return None
        elif action not in ("opened", "reopened", "synchronize", "closed"):
            return None
        if not delivery_id or len(delivery_id) > 128:
            raise ValueError("invalid delivery ID")
        return PullRequestDelivery.model_validate(payload)
    except ValueError as error:
        raise HTTPException(400, "Invalid pull request delivery") from error


@router.post("/webhooks/github", status_code=202)
async def receive_webhook(request: Request):
    secret = os.getenv("PRLENS_GITHUB_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(404)

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "Webhook body is too large")
        body.extend(chunk)

    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    delivery = parse_delivery(
        secret, request.headers.get("X-Hub-Signature-256", ""),
        request.headers.get("X-GitHub-Event"), delivery_id, body,
    )
    if delivery is None:
        return Response(status_code=204)
    try:
        await run_in_threadpool(
            accept_delivery, delivery_id, delivery.installation.id,
            delivery.repository.id, delivery.number,
        )
    except (psycopg.Error, RuntimeError) as error:
        raise HTTPException(503, "Webhook storage unavailable") from error
    return Response(status_code=202)
