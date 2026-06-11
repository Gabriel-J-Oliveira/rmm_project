from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render

from .models import ADGroup, ADUser, AclEntry, FileServer, Folder, Share


def base_context():
    return {
        'active_nav': 'access_inventory',
        'body_class': 'page-access-inventory',
    }


def dashboard(request):
    recent_acl_entries = AclEntry.objects.select_related(
        'folder',
        'folder__share',
        'folder__share__file_server',
        'ad_user',
        'ad_group',
    ).order_by('-updated_at')[:10]
    context = {
        **base_context(),
        'user_count': ADUser.objects.count(),
        'enabled_user_count': ADUser.objects.filter(enabled=True).count(),
        'group_count': ADGroup.objects.count(),
        'file_server_count': FileServer.objects.count(),
        'share_count': Share.objects.count(),
        'folder_count': Folder.objects.count(),
        'acl_count': AclEntry.objects.count(),
        'explicit_acl_count': AclEntry.objects.filter(inherited=False).count(),
        'recent_acl_entries': recent_acl_entries,
    }
    return render(request, 'access_inventory/dashboard.html', context)


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
        **base_context(),
        'users': users,
        'filters': {'q': q},
    }
    return render(request, 'access_inventory/user_list.html', context)


def user_detail(request, pk):
    user = get_object_or_404(ADUser.objects.select_related('ou'), pk=pk)
    direct_memberships = user.group_memberships.select_related('parent_group').order_by('parent_group__name')
    context = {
        **base_context(),
        'user': user,
        'direct_memberships': direct_memberships,
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
        **base_context(),
        'groups': groups,
        'filters': {'q': q},
    }
    return render(request, 'access_inventory/group_list.html', context)


def group_detail(request, pk):
    group = get_object_or_404(ADGroup.objects.select_related('ou'), pk=pk)
    memberships = group.memberships.select_related('member_user', 'member_group').order_by(
        'member_user__sam_account_name',
        'member_group__name',
    )
    acl_entries = group.acl_entries.select_related('folder', 'folder__share', 'folder__share__file_server')[:50]
    context = {
        **base_context(),
        'group': group,
        'memberships': memberships,
        'acl_entries': acl_entries,
    }
    return render(request, 'access_inventory/group_detail.html', context)


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
        **base_context(),
        'file_servers': file_servers,
        'filters': {'q': q},
    }
    return render(request, 'access_inventory/file_server_list.html', context)


def file_server_detail(request, pk):
    file_server = get_object_or_404(FileServer.objects.select_related('rmm_agent'), pk=pk)
    shares = file_server.shares.annotate(folder_count=Count('folders')).order_by('name')
    folders = Folder.objects.filter(share__file_server=file_server).select_related('share').order_by('share__name', 'path')[:200]
    context = {
        **base_context(),
        'file_server': file_server,
        'shares': shares,
        'folders': folders,
    }
    return render(request, 'access_inventory/file_server_detail.html', context)


def folder_detail(request, pk):
    folder = get_object_or_404(
        Folder.objects.select_related('share', 'share__file_server'),
        pk=pk,
    )
    acl_entries = folder.acl_entries.select_related('ad_user', 'ad_group').order_by(
        'access_type',
        'identity_name',
        'rights',
    )
    context = {
        **base_context(),
        'folder': folder,
        'acl_entries': acl_entries,
    }
    return render(request, 'access_inventory/folder_detail.html', context)
