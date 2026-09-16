from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Q
from django.utils import timezone

from access_inventory.models import ADGroupMembership, ADUser
from agents.models import AgentMachine, InventorySnapshot
from tickets.models import Ticket


OPEN_TICKET_STATUSES = {
    Ticket.STATUS_NEW,
    Ticket.STATUS_IN_PROGRESS,
    Ticket.STATUS_WAITING_USER,
    Ticket.STATUS_WAITING_THIRD_PARTY,
}


def normalize_identity(value: str | None) -> str:
    value = str(value or '').strip().casefold()
    if not value:
        return ''
    if '\\' in value:
        value = value.rsplit('\\', 1)[-1]
    if '@' in value:
        value = value.split('@', 1)[0]
    return value.strip()


def normalized_email(value: str | None) -> str:
    return str(value or '').strip().casefold()


def identity_keys(value: str | None) -> set[str]:
    raw = str(value or '').strip().casefold()
    keys = {raw} if raw else set()
    normalized = normalize_identity(raw)
    if normalized:
        keys.add(normalized)
    return {key for key in keys if key}


def ad_user_identity_keys(user: ADUser) -> set[str]:
    keys = set()
    keys.update(identity_keys(user.sam_account_name))
    keys.update(identity_keys(user.user_principal_name))
    email = normalized_email(user.email)
    if email:
        keys.add(email)
        keys.add(normalize_identity(email))
    return {key for key in keys if key}


def ticket_identity_keys(ticket: Ticket) -> set[str]:
    keys = set()
    keys.update(identity_keys(ticket.requester_username))
    email = normalized_email(ticket.requester_email)
    if email:
        keys.add(email)
        keys.add(normalize_identity(email))
    return {key for key in keys if key}


@dataclass(frozen=True)
class DirectoryMatch:
    user: ADUser | None
    status: str
    candidates: tuple[ADUser, ...] = ()


def find_ad_user_for_ticket(ticket: Ticket) -> DirectoryMatch:
    if ticket.requester_ad_user_id:
        return DirectoryMatch(ticket.requester_ad_user, 'linked')

    username_keys = identity_keys(ticket.requester_username)
    email = normalized_email(ticket.requester_email)
    query = Q()
    for key in username_keys:
        query |= Q(sam_account_name__iexact=key)
        query |= Q(user_principal_name__iexact=key)
    if email:
        query |= Q(email__iexact=email)

    if not query:
        return DirectoryMatch(None, 'no_identity')

    candidates = tuple(ADUser.objects.select_related('ou').filter(query).order_by('sam_account_name'))
    if len(candidates) == 1:
        return DirectoryMatch(candidates[0], 'matched')
    if len(candidates) > 1:
        return DirectoryMatch(None, 'ambiguous', candidates)
    return DirectoryMatch(None, 'no_match')


def ticket_queryset_for_ad_user(user: ADUser):
    query = Q(requester_ad_user=user)
    keys = ad_user_identity_keys(user)
    email = normalized_email(user.email)
    username_query = Q()
    for key in keys:
        username_query |= Q(requester_username__iexact=key)
    if email:
        username_query |= Q(requester_email__iexact=email)
    if username_query:
        query |= username_query
    return Ticket.objects.select_related('category', 'endpoint', 'sla', 'requester_ad_user').filter(query).distinct()


def summarize_ticket_counts(users):
    users = list(users)
    counters = {user.pk: {'total': 0, 'open': 0, 'last_activity': None} for user in users}
    for user in users:
        for ticket in ticket_queryset_for_ad_user(user).only('id', 'status', 'updated_at'):
            counters[user.pk]['total'] += 1
            if ticket.status in OPEN_TICKET_STATUSES:
                counters[user.pk]['open'] += 1
            last = counters[user.pk]['last_activity']
            if last is None or ticket.updated_at > last:
                counters[user.pk]['last_activity'] = ticket.updated_at
    return counters


def _endpoint_record(machine: AgentMachine, source: str, when):
    return {
        'id': str(machine.pk),
        'hostname': machine.hostname,
        'status': machine.status,
        'is_online': machine.status == AgentMachine.STATUS_ONLINE,
        'os': machine.os_name or '',
        'ip': machine.last_ip or '',
        'last_seen_at': machine.last_seen_at,
        'source': source,
        'seen_at': when or machine.last_seen_at,
        'url': f'/endpoints/{machine.pk}/',
    }


def endpoint_context_for_ad_user(user: ADUser):
    keys = ad_user_identity_keys(user)
    if not keys:
        return {
            'current': None,
            'online': [],
            'multiple_online': False,
            'history': [],
            'history_count': 0,
        }

    machines = []
    for machine in AgentMachine.objects.exclude(last_logged_user='').order_by('-last_seen_at'):
        if normalize_identity(machine.last_logged_user) in keys or normalized_email(machine.last_logged_user) in keys:
            machines.append(machine)

    snapshots = (
        InventorySnapshot.objects.select_related('machine')
        .exclude(logged_user='')
        .order_by('-received_at')
    )
    grouped = {}
    history_counts = Counter()
    first_seen = {}
    last_seen = {}
    for snapshot in snapshots:
        if normalize_identity(snapshot.logged_user) not in keys and normalized_email(snapshot.logged_user) not in keys:
            continue
        machine = snapshot.machine
        key = machine.pk
        history_counts[key] += 1
        first_seen[key] = snapshot.received_at if key not in first_seen else min(first_seen[key], snapshot.received_at)
        last_seen[key] = snapshot.received_at if key not in last_seen else max(last_seen[key], snapshot.received_at)
        grouped.setdefault(key, machine)

    for machine in machines:
        grouped.setdefault(machine.pk, machine)
        if machine.last_seen_at:
            last_seen[machine.pk] = max(last_seen.get(machine.pk, machine.last_seen_at), machine.last_seen_at)
            first_seen[machine.pk] = min(first_seen.get(machine.pk, machine.last_seen_at), machine.last_seen_at)
        history_counts[machine.pk] = max(history_counts[machine.pk], 1)

    records = []
    for key, machine in grouped.items():
        records.append({
            **_endpoint_record(machine, 'inventory', last_seen.get(key)),
            'first_seen_at': first_seen.get(key),
            'last_usage_at': last_seen.get(key),
            'usage_count': history_counts[key],
        })

    records.sort(key=lambda item: (not item['is_online'], item['last_usage_at'] or datetime.min.replace(tzinfo=timezone.utc)))
    online = [item for item in records if item['is_online']]
    current = online[0] if online else (records[0] if records else None)
    return {
        'current': current,
        'online': online,
        'multiple_online': len(online) > 1,
        'history': records,
        'history_count': len(records),
    }


def user_groups_for_profile(user: ADUser, limit=12):
    return [
        membership.parent_group
        for membership in ADGroupMembership.objects.select_related('parent_group').filter(member_user=user).order_by('parent_group__name')[:limit]
    ]


def build_recent_ticket_rows(user: ADUser, limit=6):
    return ticket_queryset_for_ad_user(user).order_by('-updated_at')[:limit]


def requester_prefill_from_ad_user(user: ADUser):
    return {
        'requester_ad_user_id': str(user.pk),
        'name': user.display_name or user.sam_account_name,
        'email': user.email or '',
        'username': user.sam_account_name or '',
        'department': user.ou.name if user.ou else '',
        'is_partner': bool(user.ou and 'SOCIO' in (user.ou.distinguished_name or '').upper()),
    }
