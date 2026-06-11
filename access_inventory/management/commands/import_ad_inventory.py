import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from access_inventory.models import ADGroup, ADGroupMembership, ADOrganizationalUnit, ADUser


def value(row, *keys, default=''):
    for key in keys:
        if key in row and row[key] not in (None, ''):
            return row[key]
    return default


def as_list(data, *keys):
    if isinstance(data, list):
        return data
    for key in keys:
        items = data.get(key)
        if isinstance(items, list):
            return items
    return []


def clean_dn(value):
    return str(value or '').strip() or None


def as_bool(raw, default=False):
    if raw in (None, ''):
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'sim', 'enabled'}


class ImportStats:
    def __init__(self):
        self.created = 0
        self.updated = 0
        self.ignored = 0

    def bump(self, created):
        if created:
            self.created += 1
        else:
            self.updated += 1


class Command(BaseCommand):
    help = 'Importa OUs, usuarios, grupos e memberships do AD a partir de um arquivo JSON.'

    def add_arguments(self, parser):
        parser.add_argument('json_path', help='Caminho do arquivo JSON de inventario AD.')

    def handle(self, *args, **options):
        json_path = Path(options['json_path'])
        if not json_path.exists():
            raise CommandError(f'Arquivo nao encontrado: {json_path}')

        try:
            data = json.loads(json_path.read_text(encoding='utf-8-sig'))
        except json.JSONDecodeError as error:
            raise CommandError(f'JSON invalido: {error}') from error

        stats = {
            'ous': ImportStats(),
            'users': ImportStats(),
            'groups': ImportStats(),
            'memberships': ImportStats(),
        }

        with transaction.atomic():
            ou_by_dn = self.import_ous(data, stats['ous'])
            users_by_sid = self.import_users(data, ou_by_dn, stats['users'])
            groups_by_sid = self.import_groups(data, ou_by_dn, stats['groups'])
            self.import_memberships(data, users_by_sid, groups_by_sid, stats['memberships'])

        self.stdout.write(self.style.SUCCESS('Importacao AD concluida.'))
        for label, item in stats.items():
            self.stdout.write(
                f'{label}: criados={item.created}, atualizados={item.updated}, ignorados={item.ignored}'
            )

    def import_ous(self, data, stats):
        ou_by_dn = {}
        for row in as_list(data, 'ous', 'organizational_units', 'organizationalUnits'):
            dn = str(value(row, 'distinguished_name', 'distinguishedName', 'dn')).strip()
            name = str(value(row, 'name', 'Name')).strip()
            if not dn or not name:
                stats.ignored += 1
                continue

            obj, created = ADOrganizationalUnit.objects.update_or_create(
                distinguished_name=dn,
                defaults={
                    'name': name,
                    'parent_distinguished_name': str(value(row, 'parent_distinguished_name', 'parentDistinguishedName', 'parent_dn', default='')).strip(),
                },
            )
            ou_by_dn[obj.distinguished_name] = obj
            stats.bump(created)
        return ou_by_dn

    def import_users(self, data, ou_by_dn, stats):
        users_by_sid = {}
        for row in as_list(data, 'users', 'ad_users', 'adUsers'):
            sid = str(value(row, 'sid', 'SID')).strip()
            if not sid:
                stats.ignored += 1
                continue

            dn = clean_dn(value(row, 'distinguished_name', 'distinguishedName', 'dn'))
            ou_dn = str(value(row, 'ou_distinguished_name', 'ouDistinguishedName', 'ou_dn', default='')).strip()
            defaults = {
                'sam_account_name': str(value(row, 'sam_account_name', 'samAccountName', 'SamAccountName')).strip(),
                'display_name': str(value(row, 'display_name', 'displayName', 'Name', default='')).strip(),
                'user_principal_name': str(value(row, 'user_principal_name', 'userPrincipalName', 'UPN', default='')).strip(),
                'email': str(value(row, 'email', 'mail', 'EmailAddress', default='')).strip(),
                'distinguished_name': dn,
                'ou': ou_by_dn.get(ou_dn),
                'enabled': as_bool(value(row, 'enabled', 'Enabled', default=True), default=True),
            }
            if not defaults['display_name']:
                defaults['display_name'] = defaults['sam_account_name']

            obj, created = ADUser.objects.update_or_create(sid=sid, defaults=defaults)
            users_by_sid[obj.sid] = obj
            stats.bump(created)
        return users_by_sid

    def import_groups(self, data, ou_by_dn, stats):
        groups_by_sid = {}
        for row in as_list(data, 'groups', 'ad_groups', 'adGroups'):
            sid = str(value(row, 'sid', 'SID')).strip()
            if not sid:
                stats.ignored += 1
                continue

            dn = clean_dn(value(row, 'distinguished_name', 'distinguishedName', 'dn'))
            ou_dn = str(value(row, 'ou_distinguished_name', 'ouDistinguishedName', 'ou_dn', default='')).strip()
            defaults = {
                'sam_account_name': str(value(row, 'sam_account_name', 'samAccountName', 'SamAccountName')).strip(),
                'name': str(value(row, 'name', 'Name', default='')).strip(),
                'description': str(value(row, 'description', 'Description', default='')).strip(),
                'distinguished_name': dn,
                'ou': ou_by_dn.get(ou_dn),
            }
            if not defaults['name']:
                defaults['name'] = defaults['sam_account_name']

            obj, created = ADGroup.objects.update_or_create(sid=sid, defaults=defaults)
            groups_by_sid[obj.sid] = obj
            stats.bump(created)
        return groups_by_sid

    def import_memberships(self, data, users_by_sid, groups_by_sid, stats):
        for row in as_list(data, 'memberships', 'group_memberships', 'groupMemberships'):
            parent_sid = str(value(row, 'parent_group_sid', 'parentGroupSid', 'group_sid', 'GroupSid')).strip()
            member_user_sid = str(value(row, 'member_user_sid', 'memberUserSid', 'user_sid', 'UserSid', default='')).strip()
            member_group_sid = str(value(row, 'member_group_sid', 'memberGroupSid', 'nested_group_sid', 'NestedGroupSid', default='')).strip()

            parent_group = groups_by_sid.get(parent_sid) or ADGroup.objects.filter(sid=parent_sid).first()
            member_user = users_by_sid.get(member_user_sid) or ADUser.objects.filter(sid=member_user_sid).first()
            member_group = groups_by_sid.get(member_group_sid) or ADGroup.objects.filter(sid=member_group_sid).first()

            if not parent_group or bool(member_user) == bool(member_group):
                stats.ignored += 1
                continue

            lookup = {'parent_group': parent_group}
            if member_user:
                lookup['member_user'] = member_user
                defaults = {'member_group': None}
            else:
                lookup['member_group'] = member_group
                defaults = {'member_user': None}

            try:
                _obj, created = ADGroupMembership.objects.update_or_create(**lookup, defaults=defaults)
            except IntegrityError:
                stats.ignored += 1
                continue
            stats.bump(created)
