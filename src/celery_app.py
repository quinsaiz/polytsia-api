from celery import Celery
from celery.schedules import crontab

from src.config import settings

celery_app = Celery("polytsia", broker=settings.celery_broker_url)

celery_app.conf.update(
    timezone=settings.timezone,
    broker_connection_retry_on_startup=True,
)
celery_app.conf.beat_schedule = {
    "refresh-movie-candidates-daily": {
        "task": "src.recommendations.tasks.refresh_movie_candidates",
        "schedule": crontab(hour=3, minute=0),
    },
    "refresh-game-candidates-daily": {
        "task": "src.recommendations.tasks.refresh_game_candidates",
        "schedule": crontab(hour=3, minute=15),
    },
}

celery_app.autodiscover_tasks(["src.recommendations"])
