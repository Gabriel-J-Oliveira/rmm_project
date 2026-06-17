from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render

from .models import (
    ADGroup,
    ADGroupMembership,
    ADOrganizationalUnit,
    ADUser,
    AccessReviewFolder,
    AccessReviewPlan,
    AccessReviewPrincipal,
    AccessReviewRule,
    AclEntry,
    FileServer,
    Folder,
    Share,
)
from .services.access_review import rule_explanation


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


def paginate(request, queryset, per_page=50):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get('page'))


def distinct_folder_count(queryset):
    return queryset.values('folder_id').distinct().count()


def direct_group_ids_for_user(user):
    return list(user.group_memberships.values_list('parent_group_id', flat=True))


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
    results = []

    if q:
        if show_all or entity_type == 'users':
            users = ADUser.objects.select_related('ou').filter(
                Q(sid__icontains=q)
                | Q(sam_account_name__icontains=q)
                | Q(display_name__icontains=q)
                | Q(email__icontains=q)
                | Q(distinguished_name__icontains=q)
            )[:20]
            results.extend([
                {
                    'type': 'user',
                    'badge': 'user',
                    'icon': 'user',
                    'name': user.display_name or user.sam_account_name,
                    'secondary': f'{user.sam_account_name} · {user.ou.name if user.ou else "sem OU"}',
                    'url_name': 'access_inventory:user-detail',
                    'id': user.id,
                }
                for user in users
            ])
        if show_all or entity_type == 'groups':
            groups = ADGroup.objects.select_related('ou').filter(
                Q(sid__icontains=q)
                | Q(sam_account_name__icontains=q)
                | Q(name__icontains=q)
                | Q(description__icontains=q)
                | Q(distinguished_name__icontains=q)
            )[:20]
            results.extend([
                {
                    'type': 'group',
                    'badge': 'group',
                    'icon': 'users',
                    'name': group.name,
                    'secondary': f'{group.sam_account_name} · {group.ou.name if group.ou else "sem OU"}',
                    'url_name': 'access_inventory:group-detail',
                    'id': group.id,
                }
                for group in groups
            ])
        if show_all or entity_type == 'ous':
            ous = ADOrganizationalUnit.objects.filter(
                Q(name__icontains=q)
                | Q(distinguished_name__icontains=q)
                | Q(parent_distinguished_name__icontains=q)
            )[:20]
            results.extend([
                {
                    'type': 'ou',
                    'badge': 'ou',
                    'icon': 'building-2',
                    'name': ou.name,
                    'secondary': ou.distinguished_name,
                    'url_name': 'access_inventory:ou-detail',
                    'id': ou.id,
                }
                for ou in ous
            ])
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
            results.extend([
                {
                    'type': 'folder',
                    'badge': 'folder',
                    'icon': 'folder-lock',
                    'name': folder.path,
                    'secondary': f'{folder.share.unc_path} · {folder.share.file_server.name}',
                    'url_name': 'access_inventory:folder-detail',
                    'id': folder.id,
                }
                for folder in folders
            ])
    else:
        recent_folders = Folder.objects.select_related('share', 'share__file_server').order_by('-updated_at')[:6]
        groups_with_acl = ADGroup.objects.filter(
            Q(resolved_acl_entries__isnull=False) | Q(acl_entries__isnull=False)
        ).distinct().annotate(acl_count=Count('resolved_acl_entries', distinct=True)).order_by('name')[:6]
        users_in_acl = ADUser.objects.filter(
            Q(resolved_acl_entries__isnull=False) | Q(acl_entries__isnull=False)
        ).distinct().order_by('sam_account_name')[:6]
        unknown_acl_entries = acl_with_relations().filter(
            resolved_identity_type=AclEntry.IDENTITY_UNKNOWN
        ).order_by('-updated_at')[:6]

    context = {
        **base_context('explorer'),
        'filters': {'q': q, 'type': entity_type},
        'results': results,
        'users': users,
        'groups': groups,
        'ous': ous,
        'folders': folders,
        'file_servers': file_servers,
        'recent_folders': locals().get('recent_folders', []),
        'groups_with_acl': locals().get('groups_with_acl', []),
        'users_in_acl': locals().get('users_in_acl', []),
        'unknown_acl_entries': locals().get('unknown_acl_entries', []),
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
    page_obj = paginate(request, users.order_by('sam_account_name'))
    context = {
        **base_context('users'),
        'page_obj': page_obj,
        'users': page_obj.object_list,
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
    direct_folder_count = distinct_folder_count(direct_acl_entries)
    group_folder_count = acl_with_relations().filter(
        Q(resolved_ad_group_id__in=direct_group_ids) | Q(ad_group_id__in=direct_group_ids)
    ).values('folder_id').distinct().count()
    context = {
        **base_context('users'),
        'user': user,
        'direct_memberships': direct_memberships,
        'direct_acl_entries': direct_acl_entries,
        'group_acl_entries': group_acl_entries,
        'direct_acl_count': direct_acl_entries.count(),
        'group_acl_count': group_acl_entries.count(),
        'direct_folder_count': direct_folder_count,
        'group_folder_count': group_folder_count,
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
    page_obj = paginate(request, groups.order_by('name'))
    context = {
        **base_context('groups'),
        'page_obj': page_obj,
        'groups': page_obj.object_list,
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
    group_acl_queryset = acl_for_group(group)
    acl_entries = group_acl_queryset.order_by('folder__path', 'access_type', 'rights')[:200]
    context = {
        **base_context('groups'),
        'group': group,
        'memberships': memberships,
        'user_members': user_members,
        'group_members': group_members,
        'acl_entries': acl_entries,
        'user_member_count': user_members.count(),
        'group_member_count': group_members.count(),
        'folder_count': distinct_folder_count(group_acl_queryset),
        'acl_count': group_acl_queryset.count(),
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
    page_obj = paginate(request, ous.order_by('distinguished_name'))
    context = {
        **base_context('ous'),
        'page_obj': page_obj,
        'ous': page_obj.object_list,
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
    user_acl_count = acl_with_relations().filter(Q(resolved_ad_user_id__in=user_ids) | Q(ad_user_id__in=user_ids)).distinct().count()
    group_acl_count = acl_with_relations().filter(Q(resolved_ad_group_id__in=group_ids) | Q(ad_group_id__in=group_ids)).distinct().count()
    context = {
        **base_context('ous'),
        'ou': ou,
        'child_ous': child_ous,
        'users': users,
        'groups': groups,
        'related_acl_entries': related_acl_entries,
        'user_acl_count': user_acl_count,
        'group_acl_count': group_acl_count,
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
    page_obj = paginate(request, file_servers.order_by('name'))
    context = {
        **base_context('folders'),
        'page_obj': page_obj,
        'file_servers': page_obj.object_list,
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
    page_obj = paginate(request, folders.order_by('share__file_server__name', 'path'))
    context = {
        **base_context('folders'),
        'page_obj': page_obj,
        'folders': page_obj.object_list,
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
    direct_user_acl_entries = acl_entries.filter(resolved_ad_user__isnull=False)
    group_acl_entries = acl_entries.filter(resolved_ad_group__isnull=False)
    unknown_acl_entries = acl_entries.filter(resolved_identity_type=AclEntry.IDENTITY_UNKNOWN)
    groups = ADGroup.objects.filter(resolved_acl_entries__folder=folder).distinct().order_by('name')
    group_accesses = []
    for group in groups:
        memberships = ADGroupMembership.objects.filter(parent_group=group, member_user__isnull=False).select_related('member_user').order_by('member_user__sam_account_name')
        member_count = memberships.count()
        group_accesses.append({
            'group': group,
            'members': memberships[:10],
            'member_count': member_count,
            'extra_count': max(member_count - 10, 0),
            'acl_entries': group_acl_entries.filter(resolved_ad_group=group),
        })
    context = {
        **base_context('folders'),
        'folder': folder,
        'acl_entries': acl_entries,
        'direct_user_acl_entries': direct_user_acl_entries,
        'group_acl_entries': group_acl_entries,
        'unknown_acl_entries': unknown_acl_entries,
        'group_accesses': group_accesses,
        'acl_count': acl_entries.count(),
        'explicit_acl_count': acl_entries.filter(inherited=False).count(),
        'inherited_acl_count': acl_entries.filter(inherited=True).count(),
        'resolved_acl_count': acl_entries.exclude(resolved_identity_type=AclEntry.IDENTITY_UNKNOWN).count(),
        'unknown_acl_count': acl_entries.filter(resolved_identity_type=AclEntry.IDENTITY_UNKNOWN).count(),
        'direct_user_count': direct_user_acl_entries.values('resolved_ad_user_id').distinct().count(),
        'group_count': group_acl_entries.values('resolved_ad_group_id').distinct().count(),
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


def review_plan_list(request):
    plans = AccessReviewPlan.objects.annotate(
        folder_count=Count('folders', distinct=True),
        rule_count=Count('rules', distinct=True),
    ).select_related('created_by')
    context = {
        **base_context('reviews'),
        'plans': plans,
    }
    return render(request, 'access_inventory/review_plan_list.html', context)


def review_plan_detail(request, plan_id):
    plan = get_object_or_404(AccessReviewPlan.objects.select_related('created_by'), pk=plan_id)
    folders = plan.folders.annotate(rule_count=Count('rules')).order_by('area_name', 'sort_order', 'proposed_path')
    area_rows = folders.values('area_name').annotate(
        folder_count=Count('id', distinct=True),
        rule_count=Count('rules', distinct=True),
        ro_count=Count('rules', filter=Q(rules__permission_level=AccessReviewRule.PERMISSION_RO), distinct=True),
        rw_count=Count('rules', filter=Q(rules__permission_level=AccessReviewRule.PERMISSION_RW), distinct=True),
    ).order_by('area_name')
    rules = plan.rules.all()
    context = {
        **base_context('reviews'),
        'plan': plan,
        'folders': folders,
        'area_rows': area_rows,
        'folder_count': folders.count(),
        'principal_count': plan.principals.count(),
        'rule_count': rules.count(),
        'ro_count': rules.filter(permission_level=AccessReviewRule.PERMISSION_RO).count(),
        'rw_count': rules.filter(permission_level=AccessReviewRule.PERMISSION_RW).count(),
        'full_custom_count': rules.filter(
            permission_level__in=[AccessReviewRule.PERMISSION_FULL, AccessReviewRule.PERMISSION_CUSTOM],
        ).count(),
        'current_folder_count': Folder.objects.count(),
        'current_acl_count': AclEntry.objects.count(),
        'ad_user_count': ADUser.objects.count(),
        'ad_group_count': ADGroup.objects.count(),
    }
    return render(request, 'access_inventory/review_plan_detail.html', context)


def review_folder_detail(request, plan_id, folder_id):
    plan = get_object_or_404(AccessReviewPlan, pk=plan_id)
    folder = get_object_or_404(
        AccessReviewFolder.objects.select_related('plan', 'parent', 'current_folder'),
        pk=folder_id,
        plan=plan,
    )
    rules = folder.rules.select_related('principal', 'principal__ad_user', 'principal__ad_group').order_by(
        'permission_level',
        'principal__display_name',
    )
    user_rules = rules.filter(principal__principal_type=AccessReviewPrincipal.PRINCIPAL_USER)
    group_rules = rules.filter(principal__principal_type=AccessReviewPrincipal.PRINCIPAL_GROUP)
    permission_order = [
        AccessReviewRule.PERMISSION_RO,
        AccessReviewRule.PERMISSION_RW,
        AccessReviewRule.PERMISSION_NONE,
        AccessReviewRule.PERMISSION_FULL,
        AccessReviewRule.PERMISSION_CUSTOM,
    ]
    permission_sections = []
    for permission_level in permission_order:
        section_rules = [
            {
                'rule': rule,
                'explanation': rule_explanation(rule),
            }
            for rule in user_rules.filter(permission_level=permission_level)
        ]
        if section_rules:
            permission_sections.append({
                'level': permission_level,
                'label': dict(AccessReviewRule.PERMISSION_LEVEL_CHOICES).get(permission_level, permission_level),
                'rules': section_rules,
            })

    technical_group_rules = [
        {
            'rule': rule,
            'explanation': rule_explanation(rule),
        }
        for rule in group_rules
    ]
    context = {
        **base_context('reviews'),
        'plan': plan,
        'folder': folder,
        'permission_sections': permission_sections,
        'technical_group_rules': technical_group_rules,
        'rule_count': rules.count(),
        'people_with_access_count': user_rules.exclude(permission_level=AccessReviewRule.PERMISSION_NONE).count(),
        'ro_count': user_rules.filter(permission_level=AccessReviewRule.PERMISSION_RO).count(),
        'rw_count': user_rules.filter(permission_level=AccessReviewRule.PERMISSION_RW).count(),
        'none_count': user_rules.filter(permission_level=AccessReviewRule.PERMISSION_NONE).count(),
        'exception_count': user_rules.filter(
            permission_level__in=[AccessReviewRule.PERMISSION_FULL, AccessReviewRule.PERMISSION_CUSTOM],
        ).count(),
        'technical_group_count': group_rules.count(),
    }
    return render(request, 'access_inventory/review_folder_detail.html', context)
