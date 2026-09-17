from __future__ import annotations

from collections import Counter, defaultdict
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


def _unique_identity_owner_map(users):
    owners = defaultdict(set)
    for user in users:
        for key in ad_user_identity_keys(user):
            owners[key].add(user.pk)
    return {key: next(iter(user_ids)) for key, user_ids in owners.items() if len(user_ids) == 1}


def summarize_ticket_counts(users):
    """Resolve ticket totals for a user collection with one ticket query."""
    users = list(users)
    counters = {user.pk: {'total': 0, 'open': 0, 'last_activity': None} for user in users}
    if not users:
        return counters

    owner_by_key = _unique_identity_owner_map(users)
    # The normalized identity may be embedded in DOMAIN\\user or a UPN, so the
    # batch query intentionally fetches only the small projection needed below.
    for ticket in Ticket.objects.values(
        'requester_ad_user_id', 'requester_username', 'requester_email', 'status', 'updated_at'
    ):
        user_id = ticket['requester_ad_user_id']
        if user_id not in counters:
            candidates = {
                owner_by_key.get(key)
                for key in ticket_identity_keys_from_values(ticket)
                if owner_by_key.get(key)
            }
            user_id = next(iter(candidates)) if len(candidates) == 1 else None
        if user_id not in counters:
            continue
        counters[user_id]['total'] += 1
        if ticket['status'] in OPEN_TICKET_STATUSES:
            counters[user_id]['open'] += 1
        last = counters[user_id]['last_activity']
        if last is None or ticket['updated_at'] > last:
            counters[user_id]['last_activity'] = ticket['updated_at']
    return counters


def ticket_identity_keys_from_values(ticket):
    keys = set(identity_keys(ticket.get('requester_username')))
    email = normalized_email(ticket.get('requester_email'))
    if email:
        keys.update({email, normalize_identity(email)})
    return {key for key in keys if key}


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
    return endpoint_context_for_ad_users([user]).get(user.pk, _empty_endpoint_context())


def _empty_endpoint_context():
    return {'current': None, 'online': [], 'multiple_online': False, 'history': [], 'history_count': 0}


def endpoint_context_for_ad_users(users):
    """Build endpoint context for many AD users with bounded query count."""
    users = list(users)
    contexts = {user.pk: _empty_endpoint_context() for user in users}
    if not users:
        return contexts

    owner_by_key = _unique_identity_owner_map(users)
    machines = list(
        AgentMachine.objects.exclude(last_logged_user='').only(
            'id', 'hostname', 'status', 'os_name', 'last_ip', 'last_seen_at', 'last_logged_user'
        )
    )
    machine_owners = {}
    for machine in machines:
        owner_id = owner_by_key.get(normalize_identity(machine.last_logged_user))
        if owner_id:
            machine_owners[machine.pk] = owner_id

    machine_by_id = {machine.pk: machine for machine in machines}
    grouped = defaultdict(dict)
    history_counts = Counter()
    first_seen = {}
    last_seen = {}
    for snapshot in InventorySnapshot.objects.exclude(logged_user='').values('machine_id', 'logged_user', 'received_at'):
        owner_id = owner_by_key.get(normalize_identity(snapshot['logged_user']))
        if not owner_id:
            continue
        machine_id = snapshot['machine_id']
        history_counts[(owner_id, machine_id)] += 1
        received_at = snapshot['received_at']
        first_key = (owner_id, machine_id)
        first_seen[first_key] = min(first_seen.get(first_key, received_at), received_at)
        last_seen[first_key] = max(last_seen.get(first_key, received_at), received_at)
        grouped[owner_id][machine_id] = machine_by_id.get(machine_id)

    for machine_id, owner_id in machine_owners.items():
        machine = machine_by_id[machine_id]
        grouped[owner_id][machine_id] = machine
        if machine.last_seen_at:
            key = (owner_id, machine_id)
            last_seen[key] = max(last_seen.get(key, machine.last_seen_at), machine.last_seen_at)
            first_seen[key] = min(first_seen.get(key, machine.last_seen_at), machine.last_seen_at)
        history_counts[(owner_id, machine_id)] = max(history_counts[(owner_id, machine_id)], 1)

    for owner_id, owner_machines in grouped.items():
        records = []
        for machine_id, machine in owner_machines.items():
            if machine is None:
                continue
            key = (owner_id, machine_id)
            records.append({
                **_endpoint_record(machine, 'inventory', last_seen.get(key)),
                'first_seen_at': first_seen.get(key),
                'last_usage_at': last_seen.get(key),
                'usage_count': history_counts[key],
            })
        records.sort(key=lambda item: (
            0 if item['is_online'] else 1,
            -(item['last_usage_at'] or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
        ))
        online = [item for item in records if item['is_online']]
        contexts[owner_id] = {
            'current': online[0] if online else (records[0] if records else None),
            'online': online,
            'multiple_online': len(online) > 1,
            'history': records,
            'history_count': len(records),
        }
    return contexts


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
