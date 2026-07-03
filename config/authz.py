from django.conf import settings


def normalized_username(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return ''
    return str(user.get_username() or '').strip().casefold()


def is_nightowl_technical_user(user):
    """MVP rule: superusers plus configured usernames can access technical areas."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    allowed = {
        str(username or '').strip().casefold()
        for username in getattr(settings, 'NIGHTOWL_TECHNICAL_USERNAMES', set())
        if str(username or '').strip()
    }
    return normalized_username(user) in allowed

