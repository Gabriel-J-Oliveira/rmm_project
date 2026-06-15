from django.utils import timezone

from access_inventory.models import ADGroup, ADUser, AclEntry


class ResolveAclIdentityResult:
    def __init__(self):
        self.processed = 0
        self.resolved_users = 0
        self.resolved_groups = 0
        self.unknown = 0
        self.updated = 0

    def as_dict(self):
        return {
            'processed': self.processed,
            'resolved_users': self.resolved_users,
            'resolved_groups': self.resolved_groups,
            'unknown': self.unknown,
            'updated': self.updated,
        }


def acl_identity_queryset(only_unknown=False, force=False):
    queryset = AclEntry.objects.all().order_by('id')
    if only_unknown:
        queryset = queryset.filter(resolved_identity_type=AclEntry.IDENTITY_UNKNOWN)
    if not force:
        queryset = queryset.filter(resolved_at__isnull=True)
    return queryset


def resolve_acl_identities(limit=None, dry_run=False, only_unknown=False, force=False):
    result = ResolveAclIdentityResult()
    queryset = acl_identity_queryset(only_unknown=only_unknown, force=force)
    if limit:
        queryset = queryset[:limit]

    user_by_sid = {
        user.sid: user
        for user in ADUser.objects.exclude(sid='')
    }
    group_by_sid = {
        group.sid: group
        for group in ADGroup.objects.exclude(sid='')
    }
    now = timezone.now()

    for acl in queryset:
        result.processed += 1
        sid = (acl.identity_sid or '').strip()
        resolved_user = user_by_sid.get(sid)
        resolved_group = None if resolved_user else group_by_sid.get(sid)

        if resolved_user:
            resolved_type = AclEntry.IDENTITY_USER
            result.resolved_users += 1
        elif resolved_group:
            resolved_type = AclEntry.IDENTITY_GROUP
            result.resolved_groups += 1
        else:
            resolved_type = AclEntry.IDENTITY_UNKNOWN
            result.unknown += 1

        if dry_run:
            continue

        acl.resolved_ad_user = resolved_user
        acl.resolved_ad_group = resolved_group
        acl.resolved_identity_type = resolved_type
        acl.resolved_at = now

        # Keep legacy fields in sync for existing screens/API consumers.
        acl.ad_user = resolved_user
        acl.ad_group = resolved_group
        acl.identity_type = resolved_type

        acl.save(update_fields=[
            'resolved_ad_user',
            'resolved_ad_group',
            'resolved_identity_type',
            'resolved_at',
            'ad_user',
            'ad_group',
            'identity_type',
            'updated_at',
        ])
        result.updated += 1

    return result
