import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import jwt
import requests

API = "https://api.github.com"
MAX_FILE_PAGES = 3


def github_request(method, path, token, **kwargs):
    response = requests.request(
        method, API + path,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=(3, 12),
        allow_redirects=False,
        **kwargs,
    )
    response.raise_for_status()
    if not response.ok or response.status_code >= 300:
        raise requests.HTTPError("Unexpected GitHub response", response=response)
    return response


def installation_token(installation_id, repository_id):
    app_id = os.environ["PRLENS_GITHUB_APP_ID"]
    key = Path(os.environ["PRLENS_GITHUB_PRIVATE_KEY_FILE"]).read_bytes()
    now = int(time.time())
    try:
        app_jwt = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, key, algorithm="RS256")
    except jwt.PyJWTError as error:
        raise ValueError("Invalid GitHub App private key") from error
    response = github_request(
        "POST", f"/app/installations/{installation_id}/access_tokens", app_jwt,
        json={"repository_ids": [repository_id], "permissions": {"pull_requests": "write"}},
    )
    return response.json()["token"]


@dataclass(frozen=True)
class Revision:
    base_ref: str
    base_sha: str
    head_sha: str


class PullRequest:
    def __init__(self, delivery):
        self.repository_id = delivery["repository_id"]
        self.token = installation_token(delivery["installation_id"], self.repository_id)
        repo = github_request("GET", f"/repositories/{self.repository_id}", self.token).json()
        self.private = repo["private"] or repo.get("visibility", "public") != "public"
        owner, name = (quote(part, safe="") for part in repo["full_name"].split("/"))
        self.path = f"/repos/{owner}/{name}/pulls/{delivery['pr_number']}"

    def revision(self):
        pull = github_request("GET", self.path, self.token).json()
        if pull["state"] != "open":
            return None
        return Revision(pull["base"]["ref"], pull["base"]["sha"], pull["head"]["sha"])

    def files(self):
        files = []
        for page in range(1, MAX_FILE_PAGES + 1):
            response = github_request(
                "GET", self.path + "/files", self.token, params={"per_page": 100, "page": page},
            )
            files.extend(response.json())
            if "next" not in response.links:
                return files, False
        return files, True

    def publish(self, revision, summary, comments):
        response = github_request(
            "POST", self.path + "/reviews", self.token,
            json={"event": "COMMENT", "commit_id": revision.head_sha, "body": summary, "comments": comments},
        )
        return response.json()["id"]

    def find_reviews(self, markers):
        bot = os.environ["PRLENS_GITHUB_BOT_LOGIN"].casefold()
        found = {}
        for page in range(1, 4):
            response = github_request(
                "GET", self.path + "/reviews", self.token, params={"per_page": 100, "page": page},
            )
            for review in response.json():
                author = review.get("user") or {}
                if author.get("login", "").casefold() == bot:
                    for marker, run_id in markers.items():
                        if marker in (review.get("body") or ""):
                            found[run_id] = review["id"]
                if len(found) == len(markers):
                    return found
            if "next" not in response.links:
                break
        return found
