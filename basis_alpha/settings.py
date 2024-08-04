"""
Django settings for basis_alpha project.

Generated by 'django-admin startproject' using Django 4.2.11.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/4.2/ref/settings/
"""
import os
from pathlib import Path

import dj_database_url
import dj_redis_url

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-m(q12ynkv)+jj&x)+8q@-0gb!*-srr$20$f(8g*o@)i5*)7-sj"

# SECURITY WARNING: don't run with debug turned on in production!
profile = os.getenv("PROFILE", "develop")
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "*").split(",")
DEBUG = profile != "production"
DEBUG = bool(os.getenv("DEBUG", DEBUG))

TESTNET = profile != "production"

SPIDER_TESTNET = str(os.getenv("SPIDER_TESTNET", TESTNET)) == "True"
# data_source spider env
SPIDER_CONSUMER_WORKERS = int(os.getenv("SPIDER_CONSUMER_WORKERS", 100))
SPIDER_WEBSOCKET_MESSAGE_QUEUE_MAX_SIZE = int(os.getenv("SPIDER_WEBSOCKET_MESSAGE_QUEUE_MAX_SIZE", 10000))

OKEX_WS_URL = os.getenv("OKEX_WS_URL", "")
OKEX_REST_URL = os.getenv("OKEX_REST_URL", "")
API_SECRET_SALT = os.getenv("API_SECRET_SALT", "1231231238888888")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN",
                           "6464788888:AAGjSMHcXK2sOuzuYBjBmjWVAdUAAAABBBB")  # replace with your token
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1829123456")  # replace with your chat id

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "data_source",
    "strategy",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "basis_alpha.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "basis_alpha.wsgi.application"

# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases


DATABASES = {
    "default": dj_database_url.config(default="mysql://root:root@127.0.0.1:3306/basis_alpha_db?charset=utf8mb4")
}

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
_parsed_redis = dj_redis_url.parse(REDIS_URL)
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2f")

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", RABBITMQ_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
CONSTANCE_REDIS_CONNECTION = REDIS_URL
CACHEOPS_REDIS = REDIS_URL

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "cache",
    }
}

# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = "static/"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")

# Extra places for collectstatic to find static files.
STATICFILES_DIRS = [os.path.join(BASE_DIR, "static")]

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOG_ENV = os.getenv("DYNO", "default")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {pathname}:{funcName}:{lineno:d} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "default": {
            "level": "INFO",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": f"{BASE_DIR}/_logs/{LOG_ENV}.log",
            "maxBytes": 1024 * 1024 * 50,  # 50 MB
            "backupCount": 5,
            "formatter": "verbose",
        },
        "error": {
            "level": "ERROR",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": f"{BASE_DIR}/_logs/{LOG_ENV}.error.log",
            "maxBytes": 1024 * 1024 * 50,  # 50 MB
            "backupCount": 5,
            "formatter": "verbose",
        },
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "sql": {
            "level": "INFO",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": f"{BASE_DIR}/_logs/{LOG_ENV}_sql.log",
            "maxBytes": 1024 * 1024 * 10,  # 10 MB
            "backupCount": 5,
            "formatter": "verbose",
        },
    },
    "loggers": {
        "jaeger_tracing": {
            "level": "ERROR",
        },
        "django.db.backends": {
            "level": "INFO",
            "handlers": ["sql"],
        },
        "": {
            "handlers": ["console", "error", "default"],
            "propagate": True,
            "level": "INFO",
        },
        "data_source": {
            "handlers": ["console", "default"],
            "propagate": False,
            "level": "DEBUG",
        },
    },
}