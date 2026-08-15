import base64
import uuid
from urllib.parse import urlparse

import requests

from .ai_agent import analyze_code_with_llm


def get_owner_and_repo(url: str):
    """Parses a GitHub repo URL and returns (owner, repo)."""
    parsed = urlparse(url)
    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) >= 2:
        return path_parts[0], path_parts[1]
    return None, None


def fetch_pr_files(repo_url: str, pr_number: int, github_token: str = None):
    """Returns the list of files changed in a given PR."""
    owner, repo = get_owner_and_repo(repo_url)
    # BUG FIX: was http:// — GitHub API requires https://
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
    headers = {"Authorization": f"token {github_token}"} if github_token else {}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


def fetch_file_content(repo_url: str, file_path: str, github_token: str = None) -> str:
    """Fetches and base64-decodes the content of a file from the repo."""
    owner, repo = get_owner_and_repo(repo_url)
    # BUG FIX: was http:// — GitHub API requires https://
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
    headers = {"Authorization": f"token {github_token}"} if github_token else {}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    content = response.json()
    return base64.b64decode(content['content']).decode()


def analyse_pr(repo_url: str, pr_number: int, github_token: str = None) -> dict:
    task_id = str(uuid.uuid4())
    try:
        pr_files = fetch_pr_files(repo_url, pr_number, github_token)
        analysis_results = []
        for file in pr_files:
            file_name = file['filename']
            raw_content = fetch_file_content(repo_url, file_name, github_token)
            analysis_result = analyze_code_with_llm(raw_content, file_name)
            analysis_results.append({"results": analysis_result, "filename": file_name})

        return {"task_id": task_id, "results": analysis_results}

    except Exception as e:
        print(f"[analyse_pr] error: {e}")
        return {"task_id": task_id, "results": [], "error": str(e)}
