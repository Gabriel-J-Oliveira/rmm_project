from django.utils.dateparse import parse_datetime

from .models import AgentJob, EndpointPerformanceSample, InventorySnapshot
from .versioning import compare_versions


MINIMUM_VERSION = '0.1.1.0-rc46'
FIELDS = ('telemetryEnabled', 'telemetrySampleSeconds', 'telemetryFlushSeconds')


def validate_configuration(value):
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError('Invalid telemetry fields.')
    enabled, sample, flush = (value[key] for key in FIELDS)
    if type(enabled) is not bool or type(sample) is not int or type(flush) is not int:
        raise ValueError('Invalid telemetry types.')
    if not 60 <= sample <= 3600 or not 60 <= flush <= 86400:
        raise ValueError('Invalid telemetry intervals.')
    return {key: value[key] for key in FIELDS}


def telemetry_capability(endpoint):
    comparison = compare_versions(endpoint.agent_version, MINIMUM_VERSION)
    if comparison is None or comparison < 0 or endpoint.has_terminal_lifecycle:
        return False, None
    heartbeat = InventorySnapshot.objects.filter(
        machine=endpoint, raw_payload__snapshot_source='heartbeat',
    ).only('raw_payload', 'received_at').order_by('-received_at').first()
    data = heartbeat.raw_payload if heartbeat else {}
    agent = data.get('agent') if isinstance(data, dict) else None
    agent = agent if isinstance(agent, dict) else {}
    compatible = (
        data.get('machine_id') == endpoint.machine_id
        and data.get('agent_version') == endpoint.agent_version
        and isinstance(agent.get('capabilities'), list)
        and 'configure_telemetry' in agent['capabilities']
    )
    return compatible, heartbeat


def validate_completion(job, result):
    if not isinstance(result, dict):
        raise ValueError('Missing telemetry confirmation.')
    if (result.get('confirmed') is not True or result.get('configuration_id') != str(job.id)
            or result.get('machine_id') != job.endpoint.machine_id):
        raise ValueError('Telemetry confirmation mismatch.')
    effective = validate_configuration(result.get('effective'))
    if effective != validate_configuration(job.payload):
        raise ValueError('Telemetry effective configuration mismatch.')
    applied = parse_datetime(str(result.get('applied_at') or ''))
    if applied is None or applied.tzinfo is None:
        raise ValueError('Missing telemetry application time.')
    return {
        'type': 'configure_telemetry', 'configuration_id': str(job.id),
        'machine_id': job.endpoint.machine_id, 'effective': effective,
        'confirmed': True, 'applied_at': applied.isoformat(),
    }


def monitoring_summary(endpoint):
    compatible, heartbeat = telemetry_capability(endpoint)
    effective = None
    reported_at = None
    if compatible and heartbeat:
        try:
            effective = validate_configuration(heartbeat.raw_payload['agent'].get('telemetry_configuration'))
            reported_at = heartbeat.received_at
        except (ValueError, KeyError, TypeError):
            pass
    job = endpoint.jobs.filter(job_type='configure_telemetry').order_by('-created_at').first()
    confirmed_at = None
    if job and job.status == AgentJob.STATUS_COMPLETED:
        try:
            result = validate_completion(job, job.result)
            confirmed_at = parse_datetime(result['applied_at'])
            if not reported_at or job.result_received_at and job.result_received_at > reported_at:
                effective = result['effective']
                reported_at = job.result_received_at
        except ValueError:
            pass
    last_sample = EndpointPerformanceSample.objects.filter(endpoint=endpoint).order_by('-collected_at').values(
        'collected_at', 'created_at',
    ).first()
    return {
        'compatible': compatible, 'minimum_version': MINIMUM_VERSION,
        'effective': effective, 'reported_at': reported_at, 'applied_at': confirmed_at,
        'requested': job.payload if job else None,
        'job_id': str(job.id) if job else None, 'job_status': job.status if job else None,
        'last_sample_at': last_sample['collected_at'] if last_sample else None,
        'last_received_at': last_sample['created_at'] if last_sample else None,
        'samples_after_application': bool(last_sample and confirmed_at and last_sample['collected_at'] >= confirmed_at),
    }
