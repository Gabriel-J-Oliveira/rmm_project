import math
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from .models import EndpointPerformanceSample


MAX_TELEMETRY_BODY_BYTES = 256 * 1024
MAX_TELEMETRY_BATCH_SAMPLES = 24
MAX_TELEMETRY_PROCESS_NAME = 128
MAX_SIGNED_BYTES = (1 << 63) - 1


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict):
            extra = set(data) - set(self.fields)
            if extra:
                raise serializers.ValidationError({'unknown_fields': sorted(extra)})
        return super().to_internal_value(data)


class FiniteFloatField(serializers.FloatField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if value is not None and not math.isfinite(value):
            raise serializers.ValidationError('Finite number required.')
        return value


def percentage():
    return FiniteFloatField(min_value=0, max_value=100, allow_null=True)


def byte_count():
    return serializers.IntegerField(min_value=0, max_value=MAX_SIGNED_BYTES, allow_null=True)


class CpuSerializer(StrictSerializer):
    usage_percent = percentage()


class MemorySerializer(StrictSerializer):
    total_bytes = byte_count()
    available_bytes = byte_count()
    used_percent = percentage()
    committed_bytes = byte_count()
    commit_limit_bytes = byte_count()
    committed_percent = percentage()


class DiskSerializer(StrictSerializer):
    active_percent = percentage()
    queue_length = FiniteFloatField(min_value=0, allow_null=True)
    read_bytes_per_sec = FiniteFloatField(min_value=0, allow_null=True)
    write_bytes_per_sec = FiniteFloatField(min_value=0, allow_null=True)
    read_latency_ms = FiniteFloatField(min_value=0, allow_null=True)
    write_latency_ms = FiniteFloatField(min_value=0, allow_null=True)


class NetworkSerializer(StrictSerializer):
    received_bytes = byte_count()
    sent_bytes = byte_count()


class ProcessSerializer(StrictSerializer):
    process_name = serializers.CharField(max_length=MAX_TELEMETRY_PROCESS_NAME)
    pid = serializers.IntegerField(min_value=0)
    cpu_percent = percentage()
    working_set_bytes = byte_count()


class ProcessesSerializer(StrictSerializer):
    cpu = ProcessSerializer(many=True)
    memory = ProcessSerializer(many=True)

    def validate(self, attrs):
        if len(attrs['cpu']) > 5 or len(attrs['memory']) > 5:
            raise serializers.ValidationError('At most five processes per category.')
        return attrs


class SampleSerializer(StrictSerializer):
    sample_id = serializers.UUIDField()
    collected_at = serializers.DateTimeField()
    uptime_seconds = serializers.IntegerField(min_value=0, max_value=MAX_SIGNED_BYTES)
    cpu = CpuSerializer()
    memory = MemorySerializer()
    disk = DiskSerializer()
    network = NetworkSerializer()
    top_processes = ProcessesSerializer()
    collection_duration_ms = serializers.IntegerField(min_value=0, max_value=60000)
    agent_working_set_bytes = byte_count()
    telemetry_errors_count = serializers.IntegerField(min_value=0, max_value=100)

    def validate_collected_at(self, value):
        now = timezone.now()
        # Accept the eight-day offline spool plus a day of clock/transport margin.
        if value > now + timedelta(minutes=5) or value < now - timedelta(days=9):
            raise serializers.ValidationError('Sample timestamp outside accepted window.')
        return value


class BatchSerializer(StrictSerializer):
    schema_version = serializers.IntegerField()
    machine_id = serializers.UUIDField()
    samples = SampleSerializer(many=True, allow_empty=False)

    def validate_schema_version(self, value):
        if value != 1:
            raise serializers.ValidationError('Unsupported telemetry schema.')
        return value

    def validate_samples(self, value):
        if len(value) > MAX_TELEMETRY_BATCH_SAMPLES:
            raise serializers.ValidationError('Batch exceeds sample limit.')
        ids = [item['sample_id'] for item in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('Duplicate sample_id in batch.')
        return value


@transaction.atomic
def persist_telemetry_batch(endpoint, samples):
    records = []
    for item in samples:
        cpu, memory, disk, network = (item[name] for name in ('cpu', 'memory', 'disk', 'network'))
        records.append(EndpointPerformanceSample(
            endpoint=endpoint,
            sample_id=item['sample_id'],
            collected_at=item['collected_at'],
            cpu_percent=cpu['usage_percent'],
            memory_total_bytes=memory['total_bytes'],
            memory_available_bytes=memory['available_bytes'],
            memory_used_percent=memory['used_percent'],
            memory_committed_bytes=memory['committed_bytes'],
            memory_commit_limit_bytes=memory['commit_limit_bytes'],
            memory_committed_percent=memory['committed_percent'],
            disk_active_percent=disk['active_percent'],
            disk_queue_length=disk['queue_length'],
            disk_read_bytes_per_sec=disk['read_bytes_per_sec'],
            disk_write_bytes_per_sec=disk['write_bytes_per_sec'],
            disk_read_latency_ms=disk['read_latency_ms'],
            disk_write_latency_ms=disk['write_latency_ms'],
            network_received_bytes=network['received_bytes'],
            network_sent_bytes=network['sent_bytes'],
            uptime_seconds=item['uptime_seconds'],
            process_consumers=item['top_processes'],
            collection_duration_ms=item['collection_duration_ms'],
            agent_working_set_bytes=item['agent_working_set_bytes'],
            telemetry_errors_count=item['telemetry_errors_count'],
        ))
    EndpointPerformanceSample.objects.bulk_create(records, ignore_conflicts=True)
