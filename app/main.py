import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional
from celery.result import AsyncResult

from app.tasks import analyse_repo_task
from app.github_app.webhook import router as github_webhook_router

app = FastAPI(
    title="PRLens",
    description="An intelligent LLM-powered Pull Request analysis service using Gemini, Celery, and Redis.",
    version="2.0.0",
)
app.include_router(github_webhook_router)


class AnalysePRRequest(BaseModel):
    repo_url: str
    pr_number: int
    github_token: Optional[str] = None


def require_manual_access(x_prlens_api_key: Optional[str] = Header(default=None)):
    key = os.getenv("PRLENS_MANUAL_API_KEY")
    if key:
        if not hmac.compare_digest(key.encode(), (x_prlens_api_key or "").encode()):
            raise HTTPException(401, "Invalid API key")
    elif os.getenv("PRLENS_GITHUB_WEBHOOK_SECRET"):
        raise HTTPException(404)


@app.post("/start_task/", summary="Start a PR analysis task", dependencies=[Depends(require_manual_access)])
async def start_task_endpoint(task_request: AnalysePRRequest):
    task = analyse_repo_task.delay(
        task_request.repo_url,
        task_request.pr_number,
        task_request.github_token,
    )
    return {"task_id": task.id, "status": "task started"}


@app.get("/task_status/{task_id}/", summary="Poll task status and result", dependencies=[Depends(require_manual_access)])
async def task_status_endpoint(task_id: str):
    result = AsyncResult(task_id)
    response = {
        "task_id": task_id,
        "status": result.state,
    }
    if result.state == "SUCCESS":
        response["result"] = result.result
    elif result.state == "FAILURE":
        response["error"] = str(result.result)
    return response
