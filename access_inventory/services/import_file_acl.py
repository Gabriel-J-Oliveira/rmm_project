from django.db import transaction

from access_inventory.models import ADGroup, ADUser, AclEntry, FileServer, Folder, Share
from agents.models import AgentMachine

from .common import ImportResult, ImportStats, as_bool, as_list, lower_choice, value


def import_file_acl_data(data):
    stats = {
        'file_servers': ImportStats(),
        'shares': ImportStats(),
        'folders': ImportStats(),
        'acl_entries': ImportStats(),
    }
    errors = list(as_list(data, 'errors', 'Errors')) if isinstance(data, dict) else []

    with transaction.atomic():
        servers_by_name = import_file_servers(data, stats['file_servers'])
        shares_by_unc = import_shares(data, servers_by_name, stats['shares'])
        folders_by_key = import_folders(data, shares_by_unc, stats['folders'])
        import_acl_entries(data, folders_by_key, stats['acl_entries'])

    return ImportResult(stats=stats, errors=errors)


def import_file_servers(data, stats):
    servers_by_name = {}
    for row in as_list(data, 'file_servers', 'fileServers', 'servers'):
        name = str(value(row, 'name', 'Name', 'hostname', 'HostName')).strip()
        if not name:
            stats.ignored += 1
            continue

        fqdn = str(value(row, 'fqdn', 'Fqdn', 'FQDN', default='')).strip()
        rmm_agent = None
        rmm_id = str(value(row, 'rmm_agent_id', 'rmmAgentId', default='')).strip()
        if rmm_id:
            rmm_agent = AgentMachine.objects.filter(id=rmm_id).first()
        if rmm_agent is None:
            rmm_agent = AgentMachine.objects.filter(hostname__iexact=name).first()
        if rmm_agent is None and fqdn:
            rmm_agent = AgentMachine.objects.filter(fqdn__iexact=fqdn).first()

        obj, created = FileServer.objects.update_or_create(
            name=name,
            defaults={
                'fqdn': fqdn,
                'description': str(value(row, 'description', 'Description', default='')).strip(),
                'rmm_agent': rmm_agent,
            },
        )
        servers_by_name[obj.name.lower()] = obj
        if obj.fqdn:
            servers_by_name[obj.fqdn.lower()] = obj
        stats.bump(created)
    return servers_by_name


def import_shares(data, servers_by_name, stats):
    shares_by_unc = {}
    for row in as_list(data, 'shares', 'Shares'):
        server_name = str(value(row, 'file_server', 'file_server_name', 'fileServer', 'server', 'Server')).strip()
        server = servers_by_name.get(server_name.lower())
        name = str(value(row, 'name', 'Name')).strip()
        unc_path = str(value(row, 'unc_path', 'uncPath', 'UNCPath')).strip()
        if not server or not name or not unc_path:
            stats.ignored += 1
            continue

        obj, created = Share.objects.update_or_create(
            unc_path=unc_path,
            defaults={
                'file_server': server,
                'name': name,
                'description': str(value(row, 'description', 'Description', default='')).strip(),
            },
        )
        shares_by_unc[obj.unc_path.lower()] = obj
        stats.bump(created)
    return shares_by_unc


def import_folders(data, shares_by_unc, stats):
    folders_by_key = {}
    for row in as_list(data, 'folders', 'Folders'):
        share_unc = str(value(row, 'share_unc_path', 'shareUncPath', 'unc_path', 'UNCPath')).strip()
        share = shares_by_unc.get(share_unc.lower())
        path = str(value(row, 'path', 'Path')).strip()
        if not share or not path:
            stats.ignored += 1
            continue

        obj, created = Folder.objects.update_or_create(
            share=share,
            path=path,
            defaults={
                'parent_path': str(value(row, 'parent_path', 'parentPath', 'ParentPath', default='')).strip(),
                'inheritance_enabled': as_bool(value(row, 'inheritance_enabled', 'inheritanceEnabled', 'InheritanceEnabled', default=True), default=True),
            },
        )
        folders_by_key[(obj.share.unc_path.lower(), obj.path.lower())] = obj
        stats.bump(created)
    return folders_by_key


def import_acl_entries(data, folders_by_key, stats):
    for row in as_list(data, 'acl_entries', 'aclEntries', 'acls', 'Acls'):
        share_unc = str(value(row, 'share_unc_path', 'shareUncPath', 'unc_path', 'UNCPath')).strip()
        folder_path = str(value(row, 'folder_path', 'folderPath', 'path', 'Path')).strip()
        folder = folders_by_key.get((share_unc.lower(), folder_path.lower()))
        if folder is None:
            folder = Folder.objects.filter(share__unc_path__iexact=share_unc, path__iexact=folder_path).first()
        if folder is None:
            stats.ignored += 1
            continue

        identity_sid = str(value(row, 'identity_sid', 'identitySid', 'SID', default='')).strip()
        ad_user = ADUser.objects.filter(sid=identity_sid).first() if identity_sid else None
        ad_group = ADGroup.objects.filter(sid=identity_sid).first() if identity_sid else None
        identity_type = lower_choice(
            value(row, 'identity_type', 'identityType', default=''),
            {'user', 'group', 'unknown'},
            'unknown',
        )
        if ad_user:
            identity_type = 'user'
        elif ad_group:
            identity_type = 'group'

        lookup = {
            'folder': folder,
            'identity_sid': identity_sid,
            'identity_name': str(value(row, 'identity_name', 'identityName', 'IdentityReference')).strip(),
            'rights': str(value(row, 'rights', 'Rights', 'fileSystemRights')).strip(),
            'access_type': lower_choice(value(row, 'access_type', 'accessType', 'AccessControlType'), {'allow', 'deny'}, 'allow'),
            'inherited': as_bool(value(row, 'inherited', 'Inherited', 'isInherited', default=False), default=False),
            'inheritance_flags': str(value(row, 'inheritance_flags', 'inheritanceFlags', 'InheritanceFlags', default='')).strip(),
            'propagation_flags': str(value(row, 'propagation_flags', 'propagationFlags', 'PropagationFlags', default='')).strip(),
            'source': str(value(row, 'source', 'Source', default='')).strip(),
        }
        if not lookup['identity_name'] or not lookup['rights']:
            stats.ignored += 1
            continue

        _obj, created = AclEntry.objects.update_or_create(
            **lookup,
            defaults={
                'identity_type': identity_type,
                'ad_user': ad_user,
                'ad_group': ad_group,
            },
        )
        stats.bump(created)
