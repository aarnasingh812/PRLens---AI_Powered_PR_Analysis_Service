from celery import Celery

app = Celery(
    'pr_analyser',
    broker='redis://127.0.0.1:6379/0',
    backend='redis://127.0.0.1:6379/0',
    include=['app.tasks']
)

app.conf.update(
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    result_expires=60 * 60 * 24,  # results expire after 24 hours
)

if __name__ == '__main__':
    app.start()
