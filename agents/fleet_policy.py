"""Administrative policy edits, deliberately independent of update dispatch."""
import hashlib
import json
import uuid
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone

from .job_progress import sanitize_job_value
from .models import AgentMachine, AgentReleaseGroup, AuditEvent
from .versioning import parse_semver


FIELDS = ('update_channel', 'update_policy', 'auto_update_enabled', 'update_paused',
          'pinned_agent_version', 'maintenance_window_start', 'maintenance_window_end',
          'maintenance_window_timezone')


class PolicyContractError(ValueError):
    def __init__(self, code, *, status=400, plan=None):
        super().__init__(code)
        self.status, self.plan = status, plan


def policy_snapshot(machine):
    result = {field: getattr(machine, field) for field in FIELDS}
    for field in ('maintenance_window_start', 'maintenance_window_end'):
        result[field] = result[field].isoformat() if result[field] else None
    result['groups'] = sorted(str(group.pk) for group in machine.rollout_groups.all())
    return result


def validate_contract(data):
    if not isinstance(data, dict) or set(data) - {'endpoint_ids', 'changes', 'reason', 'apply', 'confirmed_count', 'expected_bulk_change_hash'}:
        raise PolicyContractError('invalid_contract')
    ids = data.get('endpoint_ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 500:
        raise PolicyContractError('explicit_endpoints_required')
    try:
        ids = sorted({str(uuid.UUID(str(value))) for value in ids})
    except (ValueError, TypeError, AttributeError):
        raise PolicyContractError('invalid_endpoint_id') from None
    reason = data.get('reason')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise PolicyContractError('administrative_reason_required')
    changes = data.get('changes')
    if not isinstance(changes, dict) or not changes or set(changes) - set(FIELDS) - {'groups'}:
        raise PolicyContractError('invalid_changes')
    changes = dict(changes)
    for field, choices in [('update_channel', AgentMachine.UPDATE_CHANNEL_CHOICES), ('update_policy', AgentMachine.UPDATE_POLICY_CHOICES)]:
        if field in changes and (not isinstance(changes[field], str) or changes[field] not in dict(choices)):
            raise PolicyContractError('invalid_' + field)
    for field in ('auto_update_enabled', 'update_paused'):
        if field in changes and type(changes[field]) is not bool:
            raise PolicyContractError('invalid_' + field)
    if 'pinned_agent_version' in changes:
        pin = changes['pinned_agent_version']
        if not isinstance(pin, str) or len(pin) > 50 or (pin and parse_semver(pin) is None):
            raise PolicyContractError('invalid_pinned_version')
    for field in ('maintenance_window_start', 'maintenance_window_end'):
        if field in changes:
            try:
                if changes[field] is not None and not isinstance(changes[field], str):
                    raise ValueError()
                changes[field] = time.fromisoformat(changes[field]).isoformat() if changes[field] else None
                if changes[field] and time.fromisoformat(changes[field]).tzinfo:
                    raise ValueError()
            except (ValueError, TypeError):
                raise PolicyContractError('invalid_maintenance_window') from None
    if 'maintenance_window_timezone' in changes:
        value = changes['maintenance_window_timezone']
        if not isinstance(value, str) or len(value) > 64:
            raise PolicyContractError('invalid_timezone')
        try:
            if value:
                ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise PolicyContractError('invalid_timezone') from None
    group_change = changes.get('groups')
    if 'groups' in changes and group_change is None:
        raise PolicyContractError('invalid_group_operation')
    if group_change is not None:
        if not isinstance(group_change, dict) or set(group_change) != {'action', 'ids'} or not isinstance(group_change['action'], str) or group_change['action'] not in {'add', 'remove', 'replace', 'clear'} or not isinstance(group_change['ids'], list):
            raise PolicyContractError('invalid_group_operation')
        try:
            group_change = {'action': group_change['action'], 'ids': sorted({str(uuid.UUID(str(value))) for value in group_change['ids']})}
        except (ValueError, TypeError, AttributeError):
            raise PolicyContractError('invalid_group_id') from None
        if (group_change['action'] == 'clear' and group_change['ids']) or (group_change['action'] in {'add', 'remove'} and not group_change['ids']):
            raise PolicyContractError('invalid_group_operation')
        changes['groups'] = group_change
    if type(data.get('apply', False)) is not bool:
        raise PolicyContractError('explicit_apply_required')
    return ids, changes, sanitize_job_value(reason.strip(), max_string=1000)


def build_bulk_policy_plan(ids, changes, *, machines=None):
    groups = {str(group.pk): group.slug for group in AgentReleaseGroup.objects.all()}
    if 'groups' in changes and not set(changes['groups']['ids']) <= groups.keys():
        raise PolicyContractError('unknown_group')
    machines = machines if machines is not None else list(AgentMachine.objects.filter(pk__in=ids).order_by('pk').prefetch_related('rollout_groups'))
    found = {str(machine.pk) for machine in machines}
    rejected = [{'endpoint_id': value, 'reason': 'endpoint_not_found'} for value in ids if value not in found]
    targets = []
    for machine in machines:
        before = policy_snapshot(machine)
        after = {**before, **{key: value for key, value in changes.items() if key != 'groups'}}
        if 'groups' in changes:
            operation = changes['groups']
            existing, selected = set(before['groups']), set(operation['ids'])
            after['groups'] = sorted({'add': existing | selected, 'remove': existing - selected,
                                      'replace': selected, 'clear': set()}[operation['action']])
        protected_before = {groups[value] for value in before['groups']} & {'critical', 'servers'}
        protected_after = {groups[value] for value in after['groups']} & {'critical', 'servers'}
        rejection = ''
        if protected_before - protected_after or ((protected_before or protected_after) and (changes.get('auto_update_enabled') is True or changes.get('update_policy') == 'automatic')):
            rejection = 'protected_group_requires_separate_authorization'
        start, end = after['maintenance_window_start'], after['maintenance_window_end']
        if bool(start) != bool(end) or (start and start == end) or (after['update_policy'] == 'maintenance_window' and not start):
            rejection = 'invalid_maintenance_window'
        warnings = []
        if machine.status != 'online':
            warnings.append('endpoint_offline')
        if machine.agent_lifecycle_status != 'installed':
            warnings.append('endpoint_lifecycle_unknown')
        try:
            if uuid.UUID(machine.machine_id).int == 0:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            warnings.append('machine_identity_invalid')
        if protected_before or protected_after:
            warnings.append('protected_endpoint_group')
        jobs = list(machine.jobs.filter(job_type='update_agent', status__in=['queued', 'sent', 'running']).values('expires_at', 'created_at'))
        if jobs:
            now = timezone.now()
            warnings.append('update_job_stale' if any((job['expires_at'] and job['expires_at'] <= now) or (now - job['created_at']).total_seconds() > 900 for job in jobs) else 'update_job_active')
        if rejection:
            rejected.append({'endpoint_id': str(machine.pk), 'reason': rejection})
        targets.append({'endpoint_id': str(machine.pk), 'hostname': machine.hostname, 'before': before, 'after': after,
                        'warnings': warnings, 'rejection': rejection, 'changed': before != after})
    canonical = {'schema': 1, 'ids': ids, 'changes': changes,
                 'targets': [{key: target[key] for key in ('endpoint_id', 'before', 'after', 'rejection')} for target in targets], 'rejected': rejected}
    fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'selected_count': len(ids), 'changed_count': sum(target['changed'] and not target['rejection'] for target in targets),
            'rejected_count': len(rejected), 'targets': targets, 'rejected': rejected, 'bulk_change_hash': fingerprint, 'applied': False}


def bulk_policy_operation(data, actor):
    ids, changes, reason = validate_contract(data)
    if not data.get('apply', False):
        return build_bulk_policy_plan(ids, changes)
    if type(data.get('confirmed_count')) is not int or data['confirmed_count'] != len(ids) or not isinstance(data.get('expected_bulk_change_hash'), str):
        raise PolicyContractError('impact_confirmation_required')
    with transaction.atomic():
        machines = list(AgentMachine.objects.select_for_update().filter(pk__in=ids).order_by('pk').prefetch_related('rollout_groups'))
        plan = build_bulk_policy_plan(ids, changes, machines=machines)
        if plan['bulk_change_hash'] != data['expected_bulk_change_hash']:
            raise PolicyContractError('bulk_preview_changed', status=409, plan=plan)
        if plan['rejected']:
            raise PolicyContractError('bulk_validation_failed', status=409, plan=plan)
        by_id = {str(machine.pk): machine for machine in machines}
        for target in plan['targets']:
            if not target['changed']:
                continue
            machine = by_id[target['endpoint_id']]
            fields = []
            for field in FIELDS:
                value = target['after'][field]
                if field in changes and target['before'][field] != value:
                    setattr(machine, field, time.fromisoformat(value) if field in ('maintenance_window_start', 'maintenance_window_end') and value else value)
                    fields.append(field)
            if fields:
                machine.save(update_fields=fields + ['updated_at'])
            if target['before']['groups'] != target['after']['groups']:
                machine.rollout_groups.set(target['after']['groups'])
        if plan['changed_count']:
            # Audit failure must roll back policy changes, not silently acknowledge them.
            AuditEvent.objects.create(event_type='agent.policy.bulk_changed', title='Politicas de agentes alteradas',
                                      actor_type=AuditEvent.ACTOR_USER, actor_name=actor.get_username(),
                                      metadata={'reason': reason, 'selected_count': len(ids), 'changed_count': plan['changed_count'],
                                                'rejected_count': 0, 'fields': sorted(changes), 'timestamp': timezone.now().isoformat(),
                                                'targets': [{'endpoint_id': item['endpoint_id'],
                                                             'groups_added': sorted(set(item['after']['groups']) - set(item['before']['groups'])),
                                                             'groups_removed': sorted(set(item['before']['groups']) - set(item['after']['groups']))}
                                                            for item in plan['targets'] if item['changed']]})
        return {**plan, 'applied': True}
