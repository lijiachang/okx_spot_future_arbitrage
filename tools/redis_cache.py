import redis
from django.conf import settings

redis_cache = redis.from_url(settings.REDIS_URL)
