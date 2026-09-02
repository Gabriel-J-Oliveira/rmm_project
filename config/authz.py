from django.conf import settings
from django.db.models import Q


def normalized_username(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return ''
    return str(user.get_username() or '').strip().casefold()


def is_nightowl_technical_user(user):
    """MVP rule: staff/superusers plus configured usernames can access technical areas."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False):
        return True
    allowed = {
        str(username or '').strip().casefold()
        for username in getattr(settings, 'NIGHTOWL_TECHNICAL_USERNAMES', set())
        if str(username or '').strip()
    }
    return normalized_username(user) in allowed


def can_uninstall_agent(user):
    """Authorize destructive administrative uninstall without relying on is_staff alone."""
    if not user or not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return user.has_perm('agents.uninstall_agent')


def can_purge_agent(user):
    """Authorize destructive agent purge without treating staff or uninstall permission as sufficient."""
    if not user or not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return user.has_perm('agents.purge_agent')


def _identifier_candidates(user):
    username = str(user.get_username() or '').strip()
    email = str(getattr(user, 'email', '') or '').strip()
    candidates = {value for value in {username, email} if value}
    if '\\' in username:
        candidates.add(username.rsplit('\\', 1)[-1])
    if '@' in username:
        candidates.add(username.split('@', 1)[0])
    return candidates


def _dn_matches_allowed_ou(value):
    dn = str(value or '').casefold()
    if not dn:
        return False
    allowed_ous = {
        str(item or '').strip().casefold()
        for item in getattr(settings, 'ACCESS_INVENTORY_ALLOWED_OUS', set())
        if str(item or '').strip()
    }
    return any(ou in dn for ou in allowed_ous)


def is_access_inventory_user(user, request=None):
    """Allow Access Inventory for technical users and imported AD users in configured OUs."""
    if is_nightowl_technical_user(user):
        return True
    if not user or not getattr(user, 'is_authenticated', False):
        return False

    if request is not None and _dn_matches_allowed_ou(request.session.get('ad_distinguished_name')):
        return True

    candidates = _identifier_candidates(user)
    if not candidates:
        return False

    query = Q()
    for candidate in candidates:
        query |= Q(sam_account_name__iexact=candidate)
        query |= Q(user_principal_name__iexact=candidate)
        query |= Q(email__iexact=candidate)
    if not query:
        return False

    from access_inventory.models import ADUser

    ad_user = ADUser.objects.select_related('ou').filter(query, enabled=True).first()
    if not ad_user:
        return False
    return _dn_matches_allowed_ou(ad_user.distinguished_name) or _dn_matches_allowed_ou(
        ad_user.ou.distinguished_name if ad_user.ou else ''
    )
