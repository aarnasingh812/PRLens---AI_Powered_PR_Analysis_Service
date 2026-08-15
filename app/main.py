from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from celery.result import AsyncResult

from app.tasks import analyse_repo_task

app = FastAPI(
    title="PRLens",
    description="An intelligent LLM-powered Pull Request analysis service using Groq, Celery, and Redis.",
    version="2.0.0",
)


class AnalysePRRequest(BaseModel):
    repo_url: str
    pr_number: int
    github_token: Optional[str] = None


@app.post("/start_task/", summary="Start a PR analysis task")
async def start_task_endpoint(task_request: AnalysePRRequest):
    task = analyse_repo_task.delay(
        task_request.repo_url,
        task_request.pr_number,
        task_request.github_token,
    )
    return {"task_id": task.id, "status": "task started"}


@app.get("/task_status/{task_id}/", summary="Poll task status and result")
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
