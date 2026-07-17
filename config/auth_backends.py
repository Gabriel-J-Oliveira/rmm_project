import logging

from django.contrib.auth.backends import BaseBackend
from django.contrib.auth import get_user_model

from .ad_ldap import ActiveDirectoryError, ad_enabled, authenticate_ad_user, user_is_member_of


logger = logging.getLogger(__name__)


class ActiveDirectoryBackend(BaseBackend):
    """Authenticate users against Active Directory without disabling local users."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not ad_enabled():
            return None
        username = username or kwargs.get('username')
        try:
            user_info = authenticate_ad_user(username, password)
        except ActiveDirectoryError as exc:
            logger.warning('Active Directory authentication unavailable: %s', exc)
            return None
        if not user_info:
            return None
        if request is not None:
            request.session['ad_distinguished_name'] = user_info.distinguished_name
            request.session['ad_groups'] = user_info.groups

        UserModel = get_user_model()
        normalized_username = user_info.username.strip()
        user, _created = UserModel.objects.get_or_create(
            username=normalized_username,
            defaults={'is_active': True},
        )
        user.email = user_info.email or user.email
        user.first_name = user_info.first_name or user.first_name
        user.last_name = user_info.last_name or user.last_name
        user.is_active = True

        from django.conf import settings

        admin_group = settings.AD_AUTH_CONFIG.get('ADMIN_GROUP', '')
        tech_group = settings.AD_AUTH_CONFIG.get('TECH_GROUP', '')
        if admin_group and user_is_member_of(user_info, admin_group):
            user.is_staff = True
        elif tech_group and user_is_member_of(user_info, tech_group):
            user.is_staff = True

        user.save(update_fields=['email', 'first_name', 'last_name', 'is_active', 'is_staff'])
        return user

    def get_user(self, user_id):
        UserModel = get_user_model()
        try:
            return UserModel.objects.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
