from .settings import *  # noqa: F401,F403


# Django's test client issues internal HTTP requests; test settings must not
# inherit production HTTPS redirects from the host environment.
SECURE_SSL_REDIRECT = False

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test-nightowl.sqlite3',
    },
}

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}
