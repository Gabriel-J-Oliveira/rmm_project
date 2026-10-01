from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import authenticate_agent_token
from .models import AgentMachine, EndpointPerformanceSample
from .telemetry import BatchSerializer, MAX_TELEMETRY_BODY_BYTES, persist_telemetry_batch
from .telemetry_analytics import build_endpoint_telemetry_summary


MAX_TELEMETRY_SUMMARY_WINDOW = timedelta(days=7)


class AgentTelemetryView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        size = request.META.get('CONTENT_LENGTH')
        try:
            declared_size = int(size) if size else 0
        except ValueError:
            return Response({'error': 'invalid_content_length'}, status=status.HTTP_400_BAD_REQUEST)
        if declared_size > MAX_TELEMETRY_BODY_BYTES or len(request._request.body) > MAX_TELEMETRY_BODY_BYTES:
            return Response({'error': 'payload_too_large'}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        machine = authenticate_agent_token(request)
        serializer = BatchSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'error': 'invalid_telemetry', 'detail': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        if str(serializer.validated_data['machine_id']).lower() != str(machine.machine_id).lower():
            return Response({'error': 'machine_id_mismatch'}, status=status.HTTP_403_FORBIDDEN)
        samples = serializer.validated_data['samples']
        persist_telemetry_batch(machine, samples)
        return Response({'status': 'ok', 'accepted': len(samples)}, status=status.HTTP_200_OK)


class EndpointTelemetryView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, endpoint_id):
        if not (request.user.has_perm('agents.view_agentmachine') and request.user.has_perm('agents.view_endpointperformancesample')):
            return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)
        if not AgentMachine.objects.filter(pk=endpoint_id).exists():
            return Response({'error': 'not_found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            limit = int(request.query_params.get('limit', '100'))
        except ValueError:
            return Response({'error': 'invalid_limit'}, status=status.HTTP_400_BAD_REQUEST)
        if not 1 <= limit <= 500:
            return Response({'error': 'invalid_limit'}, status=status.HTTP_400_BAD_REQUEST)
        queryset = EndpointPerformanceSample.objects.filter(endpoint_id=endpoint_id)
        for name, lookup in (('from', 'collected_at__gte'), ('to', 'collected_at__lte')):
            if name in request.query_params:
                date = parse_datetime(request.query_params[name])
                if date is None or date.tzinfo is None:
                    return Response({'error': 'invalid_period'}, status=status.HTTP_400_BAD_REQUEST)
                queryset = queryset.filter(**{lookup: date})
        rows = queryset.order_by('-collected_at', '-id')[:limit]
        return Response({
            'endpoint_id': str(endpoint_id),
            'samples': [{
                'sample_id': str(row.sample_id),
                'collected_at': row.collected_at.isoformat(),
                'cpu_percent': row.cpu_percent,
                'memory_total_bytes': row.memory_total_bytes,
                'memory_available_bytes': row.memory_available_bytes,
                'memory_used_percent': row.memory_used_percent,
                'memory_committed_bytes': row.memory_committed_bytes,
                'memory_commit_limit_bytes': row.memory_commit_limit_bytes,
                'memory_committed_percent': row.memory_committed_percent,
                'disk_active_percent': row.disk_active_percent,
                'disk_queue_length': row.disk_queue_length,
                'disk_read_bytes_per_sec': row.disk_read_bytes_per_sec,
                'disk_write_bytes_per_sec': row.disk_write_bytes_per_sec,
                'disk_read_latency_ms': row.disk_read_latency_ms,
                'disk_write_latency_ms': row.disk_write_latency_ms,
                'network_received_bytes': row.network_received_bytes,
                'network_sent_bytes': row.network_sent_bytes,
                'uptime_seconds': row.uptime_seconds,
                'top_processes': row.process_consumers,
                'collection_duration_ms': row.collection_duration_ms,
                'agent_working_set_bytes': row.agent_working_set_bytes,
                'telemetry_errors_count': row.telemetry_errors_count,
            } for row in rows],
        })


class EndpointTelemetrySummaryView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, endpoint_id):
        if not (request.user.has_perm('agents.view_agentmachine') and request.user.has_perm('agents.view_endpointperformancesample')):
            return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)
        endpoint = AgentMachine.objects.filter(pk=endpoint_id).first()
        if endpoint is None:
            return Response({'error': 'not_found'}, status=status.HTTP_404_NOT_FOUND)
        if 'from' not in request.query_params or 'to' not in request.query_params:
            return Response({'error': 'missing_period'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            start = parse_datetime(request.query_params['from'])
            end = parse_datetime(request.query_params['to'])
        except ValueError:
            return Response({'error': 'invalid_period'}, status=status.HTTP_400_BAD_REQUEST)
        if (start is None or end is None or not timezone.is_aware(start)
                or not timezone.is_aware(end) or end <= start):
            return Response({'error': 'invalid_period'}, status=status.HTTP_400_BAD_REQUEST)
        if end - start > MAX_TELEMETRY_SUMMARY_WINDOW:
            return Response({'error': 'period_too_large'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(build_endpoint_telemetry_summary(endpoint, start, end))
