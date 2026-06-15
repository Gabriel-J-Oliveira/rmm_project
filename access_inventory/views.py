from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render

from .models import ADGroup, ADGroupMembership, ADOrganizationalUnit, ADUser, AclEntry, FileServer, Folder, Share


def base_context(section='overview'):
    return {
        'active_nav': 'access_inventory',
        'body_class': 'page-access-inventory',
        'access_active_section': section,
    }


def acl_with_relations():
    return AclEntry.objects.select_related(
        'folder',
        'folder__share',
        'folder__share__file_server',
        'ad_user',
        'ad_group',
        'resolved_ad_user',
        'resolved_ad_group',
    )


def acl_for_group(group):
    return acl_with_relations().filter(Q(resolved_ad_group=group) | Q(ad_group=group)).distinct()


def acl_for_user(user):
    return acl_with_relations().filter(Q(resolved_ad_user=user) | Q(ad_user=user)).distinct()


def dashboard(request):
    recent_acl_entries = acl_with_relations().order_by('-updated_at')[:10]
    unknown_acl_count = AclEntry.objects.filter(
        Q(resolved_identity_type=AclEntry.IDENTITY_UNKNOWN) | Q(identity_type=AclEntry.IDENTITY_UNKNOWN)
    ).count()
    context = {
        **base_context('overview'),
        'user_count': ADUser.objects.count(),
        'enabled_user_count': ADUser.objects.filter(enabled=True).count(),
        'group_count': ADGroup.objects.count(),
        'ou_count': ADOrganizationalUnit.objects.count(),
        'file_server_count': FileServer.objects.count(),
        'share_count': Share.objects.count(),
        'folder_count': Folder.objects.count(),
        'acl_count': AclEntry.objects.count(),
        'explicit_acl_count': AclEntry.objects.filter(inherited=False).count(),
        'inherited_acl_count': AclEntry.objects.filter(inherited=True).count(),
        'resolved_acl_count': AclEntry.objects.exclude(resolved_identity_type=AclEntry.IDENTITY_UNKNOWN).count(),
        'unknown_acl_count': unknown_acl_count,
        'recent_acl_entries': recent_acl_entries,
    }
    return render(request, 'access_inventory/dashboard.html', context)


def explorer(request):
    q = request.GET.get('q', '').strip()
    entity_type = request.GET.get('type', 'all').strip() or 'all'
    show_all = entity_type == 'all'

    users = ADUser.objects.none()
    groups = ADGroup.objects.none()
    ous = ADOrganizationalUnit.objects.none()
    folders = Folder.objects.none()
    file_servers = FileServer.objects.none()

    if q:
        if show_all or entity_type == 'users':
            users = ADUser.objects.select_related('ou').filter(
                Q(sid__icontains=q)
                | Q(sam_account_name__icontains=q)
                | Q(display_name__icontains=q)
                | Q(email__icontains=q)
                | Q(distinguished_name__icontains=q)
            )[:20]
        if show_all or entity_type == 'groups':
            groups = ADGroup.objects.select_related('ou').filter(
                Q(sid__icontains=q)
                | Q(sam_account_name__icontains=q)
                | Q(name__icontains=q)
                | Q(description__icontains=q)
                | Q(distinguished_name__icontains=q)
            )[:20]
        if show_all or entity_type == 'ous':
            ous = ADOrganizationalUnit.objects.filter(
                Q(name__icontains=q)
                | Q(distinguished_name__icontains=q)
                | Q(parent_distinguished_name__icontains=q)
            )[:20]
        if show_all or entity_type == 'folders':
            folders = Folder.objects.select_related('share', 'share__file_server').filter(
                Q(path__icontains=q)
                | Q(parent_path__icontains=q)
                | Q(share__name__icontains=q)
                | Q(share__unc_path__icontains=q)
                | Q(share__file_server__name__icontains=q)
                | Q(share__file_server__fqdn__icontains=q)
            )[:30]
            file_servers = FileServer.objects.filter(
                Q(name__icontains=q) | Q(fqdn__icontains=q) | Q(description__icontains=q)
            )[:10]

    context = {
        **base_context('explorer'),
        'filters': {'q': q, 'type': entity_type},
        'users': users,
        'groups': groups,
        'ous': ous,
        'folders': folders,
        'file_servers': file_servers,
    }
    return render(request, 'access_inventory/explorer.html', context)


def user_list(request):
    q = request.GET.get('q', '').strip()
    users = ADUser.objects.select_related('ou').annotate(group_count=Count('group_memberships'))
    if q:
        users = users.filter(
            Q(sid__icontains=q)
            | Q(sam_account_name__icontains=q)
            | Q(display_name__icontains=q)
            | Q(email__icontains=q)
            | Q(distinguished_name__icontains=q)
        )
    context = {
        **base_context('users'),
        'users': users,
        'filters': {'q': q},
    }
    return render(request, 'access_inventory/user_list.html', context)


def user_detail(request, pk):
    user = get_object_or_404(ADUser.objects.select_related('ou'), pk=pk)
    direct_memberships = user.group_memberships.select_related('parent_group', 'parent_group__ou').order_by('parent_group__name')
    direct_group_ids = list(direct_memberships.values_list('parent_group_id', flat=True))
    direct_acl_entries = acl_for_user(user).order_by('folder__path', 'access_type', 'rights')
    group_acl_entries = acl_with_relations().filter(
        Q(resolved_ad_group_id__in=direct_group_ids) | Q(ad_group_id__in=direct_group_ids)
    ).distinct().order_by('folder__path', 'identity_name', 'rights')[:200]
    context = {
        **base_context('users'),
        'user': user,
        'direct_memberships': direct_memberships,
        'direct_acl_entries': direct_acl_entries,
        'group_acl_entries': group_acl_entries,
        'direct_acl_count': direct_acl_entries.count(),
        'group_acl_count': group_acl_entries.count(),
    }
    return render(request, 'access_inventory/user_detail.html', context)


def group_list(request):
    q = request.GET.get('q', '').strip()
    groups = ADGroup.objects.select_related('ou').annotate(member_count=Count('memberships'))
    if q:
        groups = groups.filter(
            Q(sid__icontains=q)
            | Q(sam_account_name__icontains=q)
            | Q(name__icontains=q)
            | Q(description__icontains=q)
            | Q(distinguished_name__icontains=q)
        )
    context = {
        **base_context('groups'),
        'groups': groups,
        'filters': {'q': q},
    }
    return render(request, 'access_inventory/group_list.html', context)


def group_detail(request, pk):
    group = get_object_or_404(ADGroup.objects.select_related('ou'), pk=pk)
    memberships = group.memberships.select_related('member_user', 'member_user__ou', 'member_group', 'member_group__ou').order_by(
        'member_user__sam_account_name',
        'member_group__name',
    )
    user_members = memberships.filter(member_user__isnull=False)
    group_members = memberships.filter(member_group__isnull=False)
    acl_entries = acl_for_group(group).order_by('folder__path', 'access_type', 'rights')[:200]
    context = {
        **base_context('groups'),
        'group': group,
        'memberships': memberships,
        'user_members': user_members,
        'group_members': group_members,
        'acl_entries': acl_entries,
        'acl_count': acl_for_group(group).count(),
    }
    return render(request, 'access_inventory/group_detail.html', context)


def ou_list(request):
    q = request.GET.get('q', '').strip()
    ous = ADOrganizationalUnit.objects.annotate(
        user_count=Count('users', distinct=True),
        group_count=Count('groups', distinct=True),
    )
    if q:
        ous = ous.filter(
            Q(name__icontains=q)
            | Q(distinguished_name__icontains=q)
            | Q(parent_distinguished_name__icontains=q)
        )
    context = {
        **base_context('ous'),
        'ous': ous,
        'filters': {'q': q},
    }
    return render(request, 'access_inventory/ou_list.html', context)


def ou_detail(request, pk):
    ou = get_object_or_404(ADOrganizationalUnit, pk=pk)
    users = ou.users.order_by('sam_account_name')
    groups = ou.groups.order_by('name')
    child_ous = ADOrganizationalUnit.objects.filter(parent_distinguished_name=ou.distinguished_name).order_by('name')
    user_ids = users.values_list('id', flat=True)
    group_ids = groups.values_list('id', flat=True)
    related_acl_entries = acl_with_relations().filter(
        Q(resolved_ad_user_id__in=user_ids)
        | Q(ad_user_id__in=user_ids)
        | Q(resolved_ad_group_id__in=group_ids)
        | Q(ad_group_id__in=group_ids)
    ).distinct().order_by('folder__path', 'identity_name')[:200]
    context = {
        **base_context('ous'),
        'ou': ou,
        'child_ous': child_ous,
        'users': users,
        'groups': groups,
        'related_acl_entries': related_acl_entries,
    }
    return render(request, 'access_inventory/ou_detail.html', context)


def file_server_list(request):
    q = request.GET.get('q', '').strip()
    file_servers = FileServer.objects.select_related('rmm_agent').annotate(
        share_count=Count('shares', distinct=True),
        folder_count=Count('shares__folders', distinct=True),
    )
    if q:
        file_servers = file_servers.filter(
            Q(name__icontains=q)
            | Q(fqdn__icontains=q)
            | Q(description__icontains=q)
            | Q(rmm_agent__hostname__icontains=q)
        )
    context = {
        **base_context('folders'),
        'file_servers': file_servers,
        'filters': {'q': q},
    }
    return render(request, 'access_inventory/file_server_list.html', context)


def file_server_detail(request, pk):
    file_server = get_object_or_404(FileServer.objects.select_related('rmm_agent'), pk=pk)
    shares = file_server.shares.annotate(folder_count=Count('folders')).order_by('name')
    folders = Folder.objects.filter(share__file_server=file_server).select_related('share').order_by('share__name', 'path')[:200]
    context = {
        **base_context('folders'),
        'file_server': file_server,
        'shares': shares,
        'folders': folders,
    }
    return render(request, 'access_inventory/file_server_detail.html', context)


def folder_list(request):
    q = request.GET.get('q', '').strip()
    folders = Folder.objects.select_related('share', 'share__file_server').annotate(acl_count=Count('acl_entries'))
    if q:
        folders = folders.filter(
            Q(path__icontains=q)
            | Q(parent_path__icontains=q)
            | Q(share__name__icontains=q)
            | Q(share__unc_path__icontains=q)
            | Q(share__file_server__name__icontains=q)
            | Q(share__file_server__fqdn__icontains=q)
        )
    context = {
        **base_context('folders'),
        'folders': folders[:500],
        'filters': {'q': q},
    }
    return render(request, 'access_inventory/folder_list.html', context)


def folder_detail(request, pk):
    folder = get_object_or_404(
        Folder.objects.select_related('share', 'share__file_server'),
        pk=pk,
    )
    acl_entries = acl_with_relations().filter(folder=folder).order_by(
        'access_type',
        'identity_name',
        'rights',
    )
    context = {
        **base_context('folders'),
        'folder': folder,
        'acl_entries': acl_entries,
        'acl_count': acl_entries.count(),
        'explicit_acl_count': acl_entries.filter(inherited=False).count(),
        'inherited_acl_count': acl_entries.filter(inherited=True).count(),
        'resolved_acl_count': acl_entries.exclude(resolved_identity_type=AclEntry.IDENTITY_UNKNOWN).count(),
        'unknown_acl_count': acl_entries.filter(resolved_identity_type=AclEntry.IDENTITY_UNKNOWN).count(),
    }
    return render(request, 'access_inventory/folder_detail.html', context)


def unknown_identities(request):
    q = request.GET.get('q', '').strip()
    acl_entries = acl_with_relations().filter(resolved_identity_type=AclEntry.IDENTITY_UNKNOWN)
    if q:
        acl_entries = acl_entries.filter(
            Q(identity_sid__icontains=q)
            | Q(identity_name__icontains=q)
            | Q(folder__path__icontains=q)
            | Q(folder__share__unc_path__icontains=q)
        )
    context = {
        **base_context('unknown'),
        'acl_entries': acl_entries.order_by('identity_name', 'folder__path')[:500],
        'filters': {'q': q},
    }
    return render(request, 'access_inventory/unknown_identities.html', context)
