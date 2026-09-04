import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


INSECURE_SECRET_KEY_FALLBACK = 'django-insecure-rf)kc(p+3jf71*prhdcwpa7u&xdbzy%f%zaz8g=xr5e(i_-tmz'
DEFAULT_TECHNICAL_USERNAMES = {'gabriel.oliveira'}
SECRET_TOKEN_PATTERN = re.compile(
    r'(rmm_live_[A-Za-z0-9_\-]{8,}|deploy_[A-Za-z0-9_\-]{8,}|enroll_[A-Za-z0-9_\-]{8,}|BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY|<D>[^<]+</D>)'
)


class Command(BaseCommand):
    help = 'Executa preflight de seguranca da configuracao NightOwl sem exibir segredos.'

    def add_arguments(self, parser):
        parser.add_argument('--strict', action='store_true', help='Retorna erro quando houver qualquer FAIL.')

    def handle(self, *args, **options):
        strict = bool(options['strict'])
        self.results = []

        self._check_django(strict)
        self._check_database(strict)
        self._check_active_directory(strict)
        self._check_email()
        self._check_nightowl_urls(strict)
        self._check_git_hygiene(strict)

        final = self._final_status()
        self.stdout.write(f'SECURITY_PREFLIGHT={final}')
        if strict and final == 'FAIL':
            raise CommandError('Security preflight failed.')

    def _record(self, severity, label, *, strict_severity=None, strict=False):
        effective = strict_severity if strict and strict_severity and severity != 'PASS' else severity
        self.results.append(effective)
        self.stdout.write(f'[{effective}] {label}')

    def _check_django(self, strict):
        debug = bool(getattr(settings, 'DEBUG', False))
        self._record('WARN' if debug else 'PASS', 'DEBUG=false' if not debug else 'DEBUG=true', strict_severity='FAIL', strict=strict)

        secret_key = str(getattr(settings, 'SECRET_KEY', '') or '')
        self._record(
            'FAIL' if secret_key == INSECURE_SECRET_KEY_FALLBACK else 'PASS',
            'SECRET_KEY fallback not in use' if secret_key != INSECURE_SECRET_KEY_FALLBACK else 'SECRET_KEY fallback in use',
            strict=strict,
        )

        allowed_hosts = list(getattr(settings, 'ALLOWED_HOSTS', []) or [])
        self._record(
            'WARN' if '*' in allowed_hosts else 'PASS',
            'ALLOWED_HOSTS restricted' if '*' not in allowed_hosts else 'ALLOWED_HOSTS wildcard enabled',
            strict_severity='FAIL',
            strict=strict,
        )

        public_url = str(getattr(settings, 'NIGHTOWL_PUBLIC_URL', '') or '').rstrip('/')
        csrf_origins = {str(item).rstrip('/') for item in getattr(settings, 'CSRF_TRUSTED_ORIGINS', []) or []}
        self._record(
            'PASS' if public_url and public_url in csrf_origins else 'WARN',
            'CSRF_TRUSTED_ORIGINS covers NIGHTOWL_PUBLIC_URL' if public_url and public_url in csrf_origins else 'CSRF_TRUSTED_ORIGINS missing NIGHTOWL_PUBLIC_URL',
        )

        proxy_header = getattr(settings, 'SECURE_PROXY_SSL_HEADER', None)
        self._record('PASS' if proxy_header == ('HTTP_X_FORWARDED_PROTO', 'https') else 'WARN', 'SECURE_PROXY_SSL_HEADER configured' if proxy_header else 'SECURE_PROXY_SSL_HEADER missing')

        self._record('PASS' if bool(getattr(settings, 'SESSION_COOKIE_SECURE', False)) else 'WARN', 'SESSION_COOKIE_SECURE=true' if bool(getattr(settings, 'SESSION_COOKIE_SECURE', False)) else 'SESSION_COOKIE_SECURE=false')
        self._record('PASS' if bool(getattr(settings, 'CSRF_COOKIE_SECURE', False)) else 'WARN', 'CSRF_COOKIE_SECURE=true' if bool(getattr(settings, 'CSRF_COOKIE_SECURE', False)) else 'CSRF_COOKIE_SECURE=false')
        self._record('PASS' if bool(getattr(settings, 'SECURE_SSL_REDIRECT', False)) else 'WARN', 'SECURE_SSL_REDIRECT=true' if bool(getattr(settings, 'SECURE_SSL_REDIRECT', False)) else 'SECURE_SSL_REDIRECT=false')
        hsts_seconds = int(getattr(settings, 'SECURE_HSTS_SECONDS', 0) or 0)
        self._record('PASS' if hsts_seconds > 0 else 'WARN', 'SECURE_HSTS_SECONDS configured' if hsts_seconds > 0 else 'SECURE_HSTS_SECONDS=0')

    def _check_database(self, strict):
        database = (getattr(settings, 'DATABASES', {}) or {}).get('default', {}) or {}
        engine = str(database.get('ENGINE', '') or '')
        if 'postgresql' in engine:
            self._record('PASS', 'DATABASE engine=postgresql')
        elif 'sqlite' in engine and not bool(getattr(settings, 'DEBUG', False)):
            self._record('WARN', 'DATABASE engine=sqlite with DEBUG=false', strict_severity='FAIL', strict=strict)
        elif 'sqlite' in engine:
            self._record('WARN', 'DATABASE engine=sqlite')
        else:
            self._record('WARN', 'DATABASE engine not recognized')

    def _check_active_directory(self, strict):
        config = getattr(settings, 'AD_AUTH_CONFIG', {}) or {}
        enabled = bool(config.get('ENABLED') or getattr(settings, 'AD_AUTH_ENABLED', False))
        has_server = bool(str(config.get('SERVER_URI') or '').strip())
        has_bind_dn = bool(str(config.get('BIND_DN') or '').strip())
        has_bind_password = bool(str(config.get('BIND_PASSWORD') or '').strip())
        require_tls = bool(config.get('REQUIRE_TLS'))

        self._record('PASS' if enabled else 'WARN', 'AD enabled' if enabled else 'AD disabled')
        self._record('PASS' if has_server else ('WARN' if enabled else 'PASS'), 'AD server configured' if has_server else 'AD server not configured')
        self._record('PASS' if has_bind_dn else ('WARN' if enabled else 'PASS'), 'AD bind DN configured' if has_bind_dn else 'AD bind DN not configured')
        self._record('PASS' if has_bind_password else ('WARN' if enabled else 'PASS'), 'AD bind password configured' if has_bind_password else 'AD bind password not configured')
        if enabled and not require_tls:
            self._record('WARN', 'AD TLS not required', strict_severity='WARN', strict=strict)
        else:
            self._record('PASS', 'AD TLS required' if enabled else 'AD TLS not applicable')

    def _check_email(self):
        smtp_configured = bool(str(getattr(settings, 'EMAIL_HOST', '') or '').strip())
        password_configured = bool(str(getattr(settings, 'EMAIL_HOST_PASSWORD', '') or '').strip())
        tls_enabled = bool(getattr(settings, 'EMAIL_USE_TLS', False) or getattr(settings, 'EMAIL_USE_SSL', False))
        self._record('PASS' if smtp_configured else 'WARN', 'SMTP host configured' if smtp_configured else 'SMTP host not configured')
        self._record('PASS' if password_configured else 'WARN', 'SMTP password configured' if password_configured else 'SMTP password not configured')
        self._record('PASS' if tls_enabled else 'WARN', 'SMTP TLS/SSL enabled' if tls_enabled else 'SMTP TLS/SSL disabled')

    def _check_nightowl_urls(self, strict):
        tech_users = {str(item).casefold() for item in getattr(settings, 'NIGHTOWL_TECHNICAL_USERNAMES', set()) or set()}
        self._record(
            'WARN' if tech_users == DEFAULT_TECHNICAL_USERNAMES else 'PASS',
            'technical usernames externalized' if tech_users != DEFAULT_TECHNICAL_USERNAMES else 'technical usernames use default',
            strict_severity='WARN',
            strict=strict,
        )
        for setting_name in (
            'NIGHTOWL_PUBLIC_URL',
            'NIGHTOWL_AGENT_PUBLIC_SERVER_URL',
            'NIGHTOWL_AGENT_INSTALLER_URL',
            'NIGHTOWL_AGENT_HEARTBEAT_URL',
        ):
            value = str(getattr(settings, setting_name, '') or '').strip()
            https = urlparse(value).scheme == 'https'
            self._record('PASS' if https else 'WARN', f'{setting_name} uses HTTPS' if https else f'{setting_name} is not HTTPS', strict_severity='FAIL' if setting_name == 'NIGHTOWL_PUBLIC_URL' else None, strict=strict)

    def _check_git_hygiene(self, strict):
        root = Path(getattr(settings, 'BASE_DIR', Path.cwd()))
        tracked_files = self._git_lines(root, ['ls-files'])
        if tracked_files is None:
            self._record('WARN', 'Git repository not available for secret hygiene')
            return

        normalized = [item.replace('\\', '/') for item in tracked_files]
        env_tracked = '.env' in normalized
        self._record('FAIL' if env_tracked else 'PASS', '.env not tracked' if not env_tracked else '.env tracked', strict=strict)

        key_files = [
            path for path in normalized
            if path.lower().endswith(('.key', '.pem', '.pfx', '.p12')) and not self._is_allowed_public_or_test_fixture(path)
        ]
        self._record('FAIL' if key_files else 'PASS', 'tracked private key/certificate files absent' if not key_files else f'tracked private key/certificate files found count={len(key_files)}', strict=strict)

        secret_matches = self._count_tracked_secret_like_files(root, normalized)
        self._record('FAIL' if secret_matches else 'PASS', 'tracked token/private key patterns absent' if not secret_matches else f'tracked token/private key patterns found count={secret_matches}', strict=strict)

    def _git_lines(self, root, args):
        try:
            completed = subprocess.run(
                ['git', *args],
                cwd=root,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
            )
        except OSError:
            return None
        if completed.returncode != 0:
            return None
        return [line.strip() for line in completed.stdout.splitlines() if line.strip()]

    def _count_tracked_secret_like_files(self, root, tracked_files):
        count = 0
        for relative in tracked_files:
            if self._is_safe_to_skip_secret_scan(relative):
                continue
            path = root / relative
            try:
                content = path.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                continue
            if SECRET_TOKEN_PATTERN.search(content):
                count += 1
        return count

    def _is_allowed_public_or_test_fixture(self, path):
        lowered = path.lower()
        return any(marker in lowered for marker in ('public', 'test', 'tests', 'fixture', 'fixtures', 'release-public-keys'))

    def _is_safe_to_skip_secret_scan(self, path):
        lowered = path.lower()
        return (
            lowered.startswith(('agents/migrations/', 'static/', 'staticfiles/'))
            or '/tests' in lowered
            or lowered.endswith(('tests.py', 'settings_test.py', '.md', '.png', '.jpg', '.jpeg', '.ico', '.zip', '.dll', '.exe'))
        )

    def _final_status(self):
        if 'FAIL' in self.results:
            return 'FAIL'
        if 'WARN' in self.results:
            return 'WARN'
        return 'PASS'
