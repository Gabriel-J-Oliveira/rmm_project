from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from config.ad_ldap import (
    ActiveDirectoryConfigError,
    ActiveDirectoryUnavailable,
    ad_config,
    ad_enabled,
    find_user,
    service_connection,
    user_is_member_of,
)


class Command(BaseCommand):
    help = 'Testa configuracao AD/LDAP usando bind de servico e busca segura de usuario.'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True, help='sAMAccountName/login do usuario para buscar no AD.')

    def handle(self, *args, **options):
        username = options['username']
        config = ad_config()
        self.stdout.write(f'AD_AUTH_ENABLED: {"sim" if ad_enabled() else "nao"}')
        self.stdout.write(f'Servidor LDAP: {config.get("SERVER_URI") or "-"}')
        self.stdout.write(f'Base de usuarios: {config.get("USER_SEARCH_BASE") or "-"}')
        self.stdout.write(f'Atributo de login: {config.get("USER_ATTR") or "-"}')
        self.stdout.write(f'TLS requerido: {"sim" if config.get("REQUIRE_TLS") else "nao"}')
        if not ad_enabled():
            raise CommandError('AD_AUTH_ENABLED=False. Ative a autenticacao AD no .env para testar o dominio.')

        conn = None
        try:
            self.stdout.write('Conexao LDAP: testando bind de servico...')
            conn = service_connection()
            self.stdout.write(self.style.SUCCESS('Conexao LDAP e bind de servico OK.'))
            self.stdout.write(f'Busca de usuario: {username}')
            user_info = find_user(username, connection=conn)
        except ActiveDirectoryConfigError as exc:
            raise CommandError(f'Configuracao AD invalida: {exc}')
        except ActiveDirectoryUnavailable as exc:
            raise CommandError(f'AD indisponivel: {exc}')
        finally:
            if conn:
                try:
                    conn.unbind()
                except Exception:
                    pass

        if not user_info:
            self.stdout.write(self.style.WARNING('Usuario nao encontrado.'))
            return

        admin_group = settings.AD_AUTH_CONFIG.get('ADMIN_GROUP', '')
        tech_group = settings.AD_AUTH_CONFIG.get('TECH_GROUP', '')
        self.stdout.write(self.style.SUCCESS('Usuario encontrado no AD.'))
        self.stdout.write(f'Username: {user_info.username}')
        self.stdout.write(f'DN: {user_info.distinguished_name}')
        self.stdout.write(f'E-mail: {user_info.email or "-"}')
        self.stdout.write(f'givenName: {user_info.first_name or "-"}')
        self.stdout.write(f'sn: {user_info.last_name or "-"}')
        self.stdout.write(f'displayName: {user_info.display_name or "-"}')
        self.stdout.write(f'Nome combinado: {(user_info.first_name + " " + user_info.last_name).strip() or user_info.display_name or "-"}')
        self.stdout.write(f'Grupos encontrados: {len(user_info.groups)}')
        if admin_group:
            self.stdout.write(f'Pertence ao AD_ADMIN_GROUP: {"sim" if user_is_member_of(user_info, admin_group) else "nao"}')
        if tech_group:
            self.stdout.write(f'Pertence ao AD_TECH_GROUP: {"sim" if user_is_member_of(user_info, tech_group) else "nao"}')
        for group in user_info.groups[:20]:
            self.stdout.write(f'- {group}')
        if len(user_info.groups) > 20:
            self.stdout.write(f'... +{len(user_info.groups) - 20} grupos omitidos')
