"""Bounded chart output, merging evidence rather than averaging percentiles."""
from datetime import timedelta, datetime
from django.utils import timezone
from .models import EndpointPerformanceSample
from .telemetry_continuous import merge_metrics, mean


def historical_series(endpoint_id, hours):
    resolution = 300 if hours <= 720 else 3600
    now = timezone.now()
    buckets = {}
    rows = EndpointPerformanceSample.objects.filter(endpoint_id=endpoint_id, collected_at__gte=now-timedelta(hours=hours),
        collected_at__lt=now).only('collected_at','schema_version','metric_summaries','cpu_percent','memory_used_percent').order_by('collected_at').iterator(chunk_size=200)
    for row in rows:
        at = datetime.fromtimestamp(int(row.collected_at.timestamp())//resolution*resolution, tz=row.collected_at.tzinfo)
        target = buckets.setdefault(at, {'metrics':{}, 'kinds':set(), 'legacy_cpu':[], 'legacy_memory':[]})
        if row.schema_version == 2:
            metrics = {name:value for name,value in row.metric_summaries.get('metrics',{}).items() if name in ('cpu.usage_percent','memory.used_percent')}
            target['metrics'] = merge_metrics([target['metrics'],metrics])
            target['kinds'].add('window')
        else:
            target['kinds'].add('legacy_raw')
            if row.cpu_percent is not None: target['legacy_cpu'].append(row.cpu_percent)
            if row.memory_used_percent is not None: target['legacy_memory'].append(row.memory_used_percent)
    result = []
    for at, data in sorted(buckets.items()):
        cpu, memory = data['metrics'].get('cpu.usage_percent'), data['metrics'].get('memory.used_percent')
        # Never blend point means with duration-weighted continuous evidence.
        result.append({'at':at.isoformat(), 'resolution_seconds':resolution,
            'kind':'window' if cpu or memory else 'legacy_binned',
            'legacy_points_excluded':len(data['legacy_cpu']) if cpu else 0,
            'cpu':mean(cpu) if cpu else sum(data['legacy_cpu'])/len(data['legacy_cpu']) if data['legacy_cpu'] else None,
            'memory':mean(memory) if memory else sum(data['legacy_memory'])/len(data['legacy_memory']) if data['legacy_memory'] else None,
            'cpu_max':cpu.get('max') if cpu else None,
            'cpu_distribution':cpu.get('distribution') if cpu else None,
            'memory_distribution':memory.get('distribution') if memory else None})
    return result
