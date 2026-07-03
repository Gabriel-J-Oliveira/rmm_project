import logging
from dataclasses import dataclass, field

from django.conf import settings

try:
    from ldap3 import ALL, BASE, SUBTREE, Connection, Server
    from ldap3.core.exceptions import LDAPException
    from ldap3.utils.conv import escape_filter_chars
except ImportError:  # pragma: no cover - exercised when dependency is not installed.
    ALL = BASE = SUBTREE = Connection = Server = LDAPException = None

    def escape_filter_chars(value):
        return value


logger = logging.getLogger(__name__)


class ActiveDirectoryError(Exception):
    pass


class ActiveDirectoryConfigError(ActiveDirectoryError):
    pass


class ActiveDirectoryUnavailable(ActiveDirectoryError):
    pass


@dataclass
class ADUserInfo:
    username: str
    distinguished_name: str = ''
    email: str = ''
    first_name: str = ''
    last_name: str = ''
    display_name: str = ''
    groups: list[str] = field(default_factory=list)
    raw_attributes: dict = field(default_factory=dict)


def ad_config():
    return getattr(settings, 'AD_AUTH_CONFIG', {})


def ad_enabled():
    return bool(ad_config().get('ENABLED'))


def _require_dependency():
    if Connection is None or Server is None:
        raise ActiveDirectoryConfigError('Dependencia ldap3 nao instalada.')


def _config_value(name, default=''):
    return str(ad_config().get(name, default) or '').strip()


def validate_ad_config(require_bind=True):
    if not ad_enabled():
        raise ActiveDirectoryConfigError('AD_AUTH_ENABLED=False.')
    _require_dependency()
    missing = []
    for key in ('SERVER_URI', 'USER_SEARCH_BASE', 'USER_ATTR'):
        if not _config_value(key):
            missing.append(key)
    if require_bind and (not _config_value('BIND_DN') or not _config_value('BIND_PASSWORD')):
        missing.extend(['BIND_DN', 'BIND_PASSWORD'])
    if missing:
        raise ActiveDirectoryConfigError(f'Configuracao AD incompleta: {", ".join(missing)}.')


def _server():
    validate_ad_config(require_bind=False)
    return Server(_config_value('SERVER_URI'), get_info=ALL, connect_timeout=int(ad_config().get('TIMEOUT') or 8))


def _start_tls_if_required(connection):
    if bool(ad_config().get('REQUIRE_TLS')):
        if not connection.start_tls():
            raise ActiveDirectoryUnavailable('Nao foi possivel iniciar TLS com o servidor AD.')


def service_connection():
    validate_ad_config(require_bind=True)
    conn = Connection(
        _server(),
        user=_config_value('BIND_DN'),
        password=_config_value('BIND_PASSWORD'),
        auto_bind=False,
        receive_timeout=int(ad_config().get('TIMEOUT') or 8),
    )
    try:
        conn.open()
        _start_tls_if_required(conn)
        if not conn.bind():
            raise ActiveDirectoryUnavailable('Bind de servico AD falhou.')
    except LDAPException as exc:
        raise ActiveDirectoryUnavailable(f'Falha LDAP: {exc.__class__.__name__}') from exc
    return conn


def _first(values):
    if isinstance(values, (list, tuple)):
        return str(values[0]) if values else ''
    return '' if values is None else str(values)


def normalize_login_identifier(username):
    value = str(username or '').strip()
    if '\\' in value:
        value = value.rsplit('\\', 1)[-1]
    if '@' in value and _config_value('USER_ATTR', 'sAMAccountName').casefold() == 'samaccountname':
        value = value.split('@', 1)[0]
    return value


def _entry_to_user_info(entry, username):
    attrs = entry.entry_attributes_as_dict
    user_attr = _config_value('USER_ATTR', 'sAMAccountName')
    email_attr = _config_value('EMAIL_ATTR', 'mail')
    first_attr = _config_value('FIRST_NAME_ATTR', 'givenName')
    last_attr = _config_value('LAST_NAME_ATTR', 'sn')
    groups = [_first([value]) for value in attrs.get('memberOf', [])]
    return ADUserInfo(
        username=_first(attrs.get(user_attr)) or username,
        distinguished_name=entry.entry_dn,
        email=_first(attrs.get(email_attr)),
        first_name=_first(attrs.get(first_attr)),
        last_name=_first(attrs.get(last_attr)),
        display_name=_first(attrs.get('displayName')) or _first(attrs.get('cn')),
        groups=[group for group in groups if group],
        raw_attributes=attrs,
    )


def find_user(username, connection=None):
    if not username:
        return None
    search_username = normalize_login_identifier(username)
    owns_connection = connection is None
    conn = connection or service_connection()
    user_attr = _config_value('USER_ATTR', 'sAMAccountName')
    escaped = escape_filter_chars(search_username)
    search_filter = f'(&(objectClass=user)({user_attr}={escaped}))'
    attributes = {
        user_attr,
        _config_value('EMAIL_ATTR', 'mail'),
        _config_value('FIRST_NAME_ATTR', 'givenName'),
        _config_value('LAST_NAME_ATTR', 'sn'),
        'displayName',
        'cn',
        'memberOf',
        'userPrincipalName',
    }
    try:
        found = conn.search(
            search_base=_config_value('USER_SEARCH_BASE'),
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=list(attributes),
            size_limit=2,
        )
        if not found or not conn.entries:
            return None
        return _entry_to_user_info(conn.entries[0], search_username)
    finally:
        if owns_connection:
            conn.unbind()


def _bind_identity(user_info, username):
    if user_info and user_info.distinguished_name:
        return user_info.distinguished_name
    domain = _config_value('DOMAIN')
    realm = _config_value('REALM')
    if domain and '\\' not in username and '@' not in username:
        return f'{domain}\\{username}'
    if realm and '\\' not in username and '@' not in username:
        return f'{username}@{realm}'
    return username


def authenticate_ad_user(username, password):
    if not ad_enabled() or not username or not password:
        return None
    conn = service_connection()
    try:
        user_info = find_user(username, connection=conn)
    finally:
        conn.unbind()
    if not user_info:
        return None

    bind_user = _bind_identity(user_info, username)
    user_conn = Connection(
        _server(),
        user=bind_user,
        password=password,
        auto_bind=False,
        receive_timeout=int(ad_config().get('TIMEOUT') or 8),
    )
    try:
        user_conn.open()
        _start_tls_if_required(user_conn)
        if not user_conn.bind():
            return None
        return user_info
    except LDAPException as exc:
        logger.warning('AD authentication failed due to LDAP error: %s', exc.__class__.__name__)
        return None
    finally:
        try:
            user_conn.unbind()
        except Exception:
            pass


def _normalize_group_values(value):
    value = str(value or '').strip()
    if not value:
        return set()
    lowered = value.casefold()
    values = {lowered}
    for part in value.split(','):
        part = part.strip()
        if part.upper().startswith('CN='):
            values.add(part[3:].casefold())
    return values


def user_is_member_of(user_info, configured_group):
    expected = _normalize_group_values(configured_group)
    if not expected or not user_info:
        return False
    for group in user_info.groups:
        if expected.intersection(_normalize_group_values(group)):
            return True
    return False


def diagnostic_search(username):
    conn = service_connection()
    try:
        user_info = find_user(username, connection=conn)
        return user_info
    finally:
        conn.unbind()
