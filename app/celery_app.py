import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

app = Celery(
    'pr_analyser',
    broker=os.getenv("PRLENS_REDIS_URL", "redis://127.0.0.1:6379/0"),
    backend=os.getenv("PRLENS_REDIS_URL", "redis://127.0.0.1:6379/0"),
    include=["app.tasks", "app.github_app.tasks"],
)

app.conf.update(
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    result_expires=60 * 60 * 24,  # results expire after 24 hours
)

if os.getenv("PRLENS_DATABASE_URL"):
    app.conf.beat_schedule = {
        "dispatch-github-deliveries": {
            "task": "app.github_app.tasks.dispatch_github_deliveries",
            "schedule": 5.0,
        },
    }

if __name__ == '__main__':
    app.start()
