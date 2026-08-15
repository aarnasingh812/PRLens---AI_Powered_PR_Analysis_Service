from app.celery_app import app
from app.utils.github import analyse_pr


@app.task
def analyse_repo_task(repo_url: str, pr_number: int, github_token: str = None):
    """
    Celery task that runs the full PR analysis pipeline.
    Returns a dict with task_id and per-file analysis results.
    """
    return analyse_pr(repo_url, pr_number, github_token)
