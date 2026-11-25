from bisect import bisect_right
from datetime import datetime, timedelta
from math import isfinite
from typing import NamedTuple

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import AgentJob, AgentMachine, AlertEvent, AlertPolicy, EndpointAlert

SOURCE = 'alert_policy'
TYPES = {'cpu': 'cpu_pressure', 'ram': 'memory_pressure', 'disk': 'disk_low',
         'offline': 'endpoint_offline', 'uptime': 'high_uptime',
         'antivirus': 'security_antivirus', 'job_failures': 'job_failures'}
UNITS = {'cpu': '%', 'ram': '%', 'disk': '%', 'offline': 'h', 'uptime': 'dias', 'job_failures': 'falhas'}
TELEMETRY = {'cpu': 'cpu.usage_percent', 'ram': 'memory.used_percent'}
SEVERITY = {'critical': 0, 'warning': 1, 'info': 2}
HISTORY_LIMIT = 1441


class Reading(NamedTuple):
    start: datetime
    end: datetime
    low: float
    high: float


def _numeric(value):
    return type(value) in (int, float) and isfinite(value)


def _fresh(at, now, minutes):
    return at is not None and minutes and timedelta(0) <= now - at <= timedelta(minutes=minutes)


def _telemetry(rule, windows, now):
    if not windows or not _fresh(windows[0].window_end_at, now, rule.max_age_minutes):
        return {}
    readings = []
    for row in windows:
        summary = row.metric_summaries
        metric = summary.get('metrics', {}).get(TELEMETRY[rule.metric], {})
        low, high = metric.get('min'), metric.get('max')
        count = metric.get('count')
        if (not rule.min_coverage_percent or type(count) is not int or not 0 < count <= 6
                or count * 100 < 6 * rule.min_coverage_percent
                or not (_numeric(low) and _numeric(high) and 0 <= low <= high <= 100)):
            break
        readings.append(Reading(row.collected_at, row.window_end_at, low, high))
    return {'': readings}


def _inventory_values(rule, snapshot):
    if rule.metric == 'uptime':
        seconds = snapshot.uptime_seconds
        return {'': seconds / 86400} if seconds is not None and seconds >= 0 else {}
    if rule.metric == 'disk':
        values = {}
        for disk in snapshot.disks:
            if not isinstance(disk, dict):
                continue
            name, size, free = disk.get('name'), disk.get('size_bytes'), disk.get('free_bytes')
            if isinstance(name, str) and name and _numeric(size) and _numeric(free) and size > 0 and 0 <= free <= size:
                values[name] = (size - free) * 100 / size
        return values
    status = snapshot.defender_status or {}
    if not isinstance(status, dict):
        return {}
    raw = status.get('raw') or {}
    if not isinstance(raw, dict):
        return {}
    inner = raw.get('raw') or {}
    if raw.get('error') or not isinstance(inner, dict) or inner.get('error'):
        return {}
    enabled, realtime = status.get('enabled'), status.get('real_time_protection_enabled')
    if raw.get('defender_present') is not False and enabled is True and realtime is True:
        return {'': 0}
    if raw.get('state') == 'not_present':
        condition = 'missing'
    elif raw.get('defender_present') is True and (enabled is False or realtime is False):
        condition = 'disabled'
    else:
        return {}
    return {'': 1} if rule.antivirus_condition in (condition, 'either') else {}


def _inventory_timestamp(rule, snapshot):
    field, section = {
        'disk': ('disks', 'disk'), 'uptime': ('uptime_seconds', 'system'),
        'antivirus': ('defender_status', 'security'),
    }[rule.metric]
    raw = snapshot.raw_payload or {}
    observation = (raw.get('inventory_observations') or {}).get(section, {})
    if observation.get('status') in {'failed', 'unknown', 'not_present'}:
        return None
    heartbeat = raw.get('heartbeat_inventory') or {}
    if field in heartbeat.get('unknown_fields', []):
        return None
    if raw.get('snapshot_source') == 'heartbeat' or heartbeat:
        if field in heartbeat.get('observed_fields', []):
            return snapshot.collected_at
        inherited = (heartbeat.get('inherited') or {}).get(field, {})
        return parse_datetime(inherited.get('collected_at') or '')
    if raw.get('snapshot_source') == 'collection':
        collection = (raw.get('collections') or {}).get(section) or {}
        return parse_datetime(collection.get('collected_at') or observation.get('last_success_at') or '')
    return snapshot.collected_at


def _inventory(rule, snapshots, now):
    if not snapshots:
        return {}
    latest = _inventory_timestamp(rule, snapshots[0])
    if not _fresh(latest, now, rule.max_age_minutes):
        return {}
    observations = []
    for snapshot in snapshots:
        at = _inventory_timestamp(rule, snapshot)
        if at is None or at > latest:
            break
        if observations and at >= observations[-1][0]:
            continue
        observations.append((at, _inventory_values(rule, snapshot)))
        if latest - at >= timedelta(minutes=rule.duration_minutes):
            break
    values = {}
    for resource in observations[0][1]:
        series = []
        for at, readings in observations:
            if resource not in readings or (series and not _fresh(at, series[-1].start, rule.max_age_minutes)):
                break
            value = readings[resource]
            series.append(Reading(at, series[-1].start if series else at, value, value))
        values[resource] = series
    return values


def _evidence(rule, endpoint, windows, inventory, jobs, now):
    if rule.metric in TELEMETRY:
        return _telemetry(rule, windows, now)
    if rule.metric == 'offline':
        if not endpoint.last_seen_at or endpoint.last_seen_at > now:
            return {}
        if endpoint.status == AgentMachine.STATUS_ONLINE:
            return {'': [Reading(now, now, 0, 0)]}
        if endpoint.status == AgentMachine.STATUS_OFFLINE:
            age = (now - endpoint.last_seen_at).total_seconds() / 3600
            start = endpoint.last_seen_at + timedelta(hours=rule.threshold) if age >= rule.threshold else now
            return {'': [Reading(start, now, age, age)]}
        return {}
    if rule.metric == 'job_failures':
        if not rule.job_window_minutes or not _fresh(endpoint.last_seen_at, now, rule.max_age_minutes):
            return {}
        window = timedelta(minutes=rule.job_window_minutes)
        lower = _bounded_timestamp(now, rule.duration_minutes)
        changes = {time for time in jobs if lower < time < now}
        changes.update(time + window for time in jobs if now - time > window and time + window > lower)
        changes.update((lower, now))
        readings = []
        for at in sorted(changes, reverse=True):
            count = bisect_right(jobs, at) - bisect_right(jobs, _bounded_timestamp(at, rule.job_window_minutes))
            readings.append(Reading(at, readings[-1].start if readings else at, count, count))
        return {'': readings}
    return _inventory(rule, inventory, now)


def _breach(readings, threshold, minutes):
    end = readings[0].end
    previous = None
    for reading in readings:
        if reading.low < threshold or (previous and previous.start != reading.end):
            break
        if end - reading.start >= timedelta(minutes=minutes):
            return True
        previous = reading
    return False


def _matches(rule, endpoint, snapshot):
    if rule.scope == AlertPolicy.Scope.ALL:
        return True
    if rule.scope == AlertPolicy.Scope.ENDPOINTS:
        return any(target.pk == endpoint.pk for target in rule.endpoints.all())
    from dashboard.views import infer_endpoint_type

    category = infer_endpoint_type(endpoint.hostname, snapshot.os_name if snapshot else '', snapshot)
    return ((rule.scope == AlertPolicy.Scope.SERVERS and category == 'server')
            or (rule.scope == AlertPolicy.Scope.DESKTOPS and category in ('workstation', 'notebook')))


def _priority(rule):
    scopes = {'endpoints': 0, 'servers': 1, 'desktops': 1, 'all': 2}
    return SEVERITY[rule.severity], scopes[rule.scope], -(rule.threshold or 0), rule.key


def _bounded_timestamp(now, minutes):
    elapsed = now - datetime.min.replace(tzinfo=now.tzinfo)
    return now - min(elapsed, timedelta(minutes=minutes))


def _decisions(endpoint, policies, now):
    rules = [rule for rule in policies if rule.metric in TYPES and rule.severity in SEVERITY
             and (rule.antivirus_condition if rule.metric == 'antivirus'
                  else rule.threshold is not None and rule.recovery_threshold is not None)]
    if not rules:
        return {}, {}
    inventory = list(endpoint.inventory_snapshots.filter(collected_at__lte=now)
                     .order_by('-collected_at', '-received_at')[:HISTORY_LIMIT])
    rules = [rule for rule in rules if _matches(rule, endpoint, inventory[0] if inventory else None)]
    windows, jobs = [], []
    if any(rule.metric in TELEMETRY for rule in rules):
        minutes = min(HISTORY_LIMIT, max(rule.duration_minutes + (rule.max_age_minutes or 0) + 1
                                        for rule in rules if rule.metric in TELEMETRY))
        windows = list(endpoint.performance_samples.filter(
            schema_version=2, resolution_seconds=60,
            window_end_at__range=(now - timedelta(minutes=minutes), now),
        ).order_by('-collected_at')[:HISTORY_LIMIT])
    job_rules = [rule for rule in rules if rule.metric == 'job_failures' and rule.job_window_minutes]
    if job_rules:
        lookback = max(rule.duration_minutes + rule.job_window_minutes for rule in job_rules)
        jobs = list(endpoint.jobs.filter(
            status=AgentJob.STATUS_FAILED, result_received_at__range=(_bounded_timestamp(now, lookback), now),
        ).order_by('result_received_at').values_list('result_received_at', flat=True))
    firing, current = {}, {}
    for rule in sorted(rules, key=_priority):
        threshold = 1 if rule.metric == 'antivirus' else rule.threshold
        recovery = 0 if rule.metric == 'antivirus' else rule.recovery_threshold
        for resource, readings in _evidence(rule, endpoint, windows, inventory, jobs, now).items():
            if not readings:
                continue
            key = f'{TYPES[rule.metric]}:{resource}' if resource else TYPES[rule.metric]
            current[key, rule.pk] = readings[0], recovery
            if _breach(readings, threshold, rule.duration_minutes):
                firing.setdefault(key, (rule, resource, readings[0]))
    return firing, current


@transaction.atomic
def evaluate_policies(endpoint, policies, *, dry_run=False):
    from .alerting import audit_alert, create_event

    if not dry_run:
        endpoint = AgentMachine.objects.select_for_update().get(pk=endpoint.pk)
    now = timezone.now()
    policy_index = {rule.pk: rule for rule in policies}
    active_statuses = (EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED)
    alerts = endpoint.alerts.filter(status__in=active_statuses, alert_type__in=TYPES.values(),
                                    is_temporary=False, source__in=('', SOURCE)).order_by('first_seen_at', 'pk')
    existing = {}
    for alert in alerts:
        existing.setdefault(alert.metadata.get('dedupe_key'), alert)
    owners = {alert.metadata.get('policy') for alert in existing.values() if alert.source == SOURCE}
    firing, current = _decisions(endpoint, [rule for rule in policies if rule.enabled or rule.pk in owners], now)
    for key, alert in existing.items():
        if alert.source != SOURCE or key not in firing:
            continue
        next_rule = firing[key][0]
        if SEVERITY.get(alert.severity, 3) < SEVERITY[next_rule.severity]:
            observation = current.get((key, alert.metadata.get('policy')))
            if not observation or observation[0].high > observation[1]:
                del firing[key]
    managed = set(firing)
    actions = []
    counts = dict(created=0, updated=0, resolved=0)
    for key, (rule, resource, reading) in firing.items():
        alert = existing.get(key)
        metadata = {'dedupe_key': key, 'policy': rule.key, 'policy_revision': rule.revision,
                    'metric': rule.metric, 'reading_at': reading.end.isoformat(),
                    'threshold': rule.threshold, 'value': reading.low, 'quality': 'current',
                    'notify_visual': rule.notify_visual, 'notify_sound': rule.notify_sound}
        unit = UNITS.get(rule.metric)
        description = (f'{reading.low:.1f} {unit} (limite {rule.threshold} {unit}).'
                       if unit else rule.get_antivirus_condition_display())
        if resource:
            metadata.update(disk_name=resource, used_percent=round(reading.low, 1),
                            free_percent=round(100 - reading.low, 1))
        changed = not alert or alert.source != SOURCE or alert.severity != rule.severity or alert.metadata.get('policy') != rule.key
        if alert and not changed and alert.metadata == metadata and alert.title == rule.name:
            continue
        counts['updated' if alert else 'created'] += 1
        if dry_run:
            actions.append(f'policy {rule.pk}: {endpoint.pk} {key}')
            continue
        creating = alert is None
        if creating:
            alert = EndpointAlert(endpoint=endpoint, alert_type=TYPES[rule.metric], first_seen_at=now)
        alert.title = rule.name
        alert.description = description
        alert.severity = rule.severity
        alert.last_seen_at = now
        alert.source = SOURCE
        alert.metadata = metadata
        if creating:
            alert.save()
        else:
            alert.save(update_fields=['title', 'description', 'severity', 'last_seen_at', 'source',
                                      'metadata', 'updated_at'])
        if changed:
            create_event(alert, AlertEvent.TYPE_CREATED if creating else AlertEvent.TYPE_UPDATED,
                         'Ocorrência avaliada por política.', metadata)
            audit_alert(alert, 'alert.created' if creating else 'alert.updated', alert.title, description)

    for key, alert in existing.items():
        if alert.source != SOURCE or key in firing:
            continue
        managed.add(key)
        observation = current.get((key, alert.metadata.get('policy')))
        if not observation or observation[0].high > observation[1]:
            rule = policy_index.get(alert.metadata.get('policy'))
            at = parse_datetime(alert.metadata.get('reading_at') or '')
            quality = 'current' if observation else 'unknown'
            if not observation and rule and rule.max_age_minutes and at and not _fresh(at, now, rule.max_age_minutes):
                quality = 'stale'
            if not dry_run and alert.metadata.get('quality') != quality:
                alert.metadata = {**alert.metadata, 'quality': quality}
                alert.save(update_fields=['metadata', 'updated_at'])
            continue
        counts['resolved'] += 1
        if dry_run:
            actions.append(f'policy resolve: {endpoint.pk} {key}')
            continue
        alert.status = EndpointAlert.STATUS_RESOLVED
        alert.resolved_at = now
        alert.resolution_type = EndpointAlert.RESOLUTION_AUTOMATIC
        alert.save(update_fields=['status', 'resolved_at', 'resolution_type', 'updated_at'])
        create_event(alert, AlertEvent.TYPE_RESOLVED_AUTOMATIC, 'Recuperação confirmada por evidência.')
        audit_alert(alert, 'alert.resolved_auto', alert.title, 'Recuperação confirmada por evidência.')
    return managed, counts, actions
