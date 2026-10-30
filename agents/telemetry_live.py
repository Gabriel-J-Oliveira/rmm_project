"""Outbound-only live telemetry. Durable leases, ephemeral points, no jobs."""
import json
import secrets
import uuid
import os
from pathlib import Path
from datetime import timedelta

from django.core.cache import caches
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.authz import is_nightowl_technical_user
from .authentication import authenticate_agent_token
from .models import AgentMachine, TelemetryLiveSession
from .telemetry_continuous import number


def shared_cache():
    cache = caches['telemetry_live']
    if cache.__class__.__module__.endswith(('.locmem', '.dummy')):
        raise ValueError('shared_live_cache_required')
    if hasattr(cache, '_dir'):
        location = Path(cache._dir).resolve()
        from django.conf import settings
        for name in ('MEDIA_ROOT', 'STATIC_ROOT'):
            public = getattr(settings, name, None)
            if public and location.is_relative_to(Path(public).resolve()):
                raise ValueError('live_cache_must_not_be_web_served')
    if os.name != 'nt' and hasattr(cache, '_dir') and Path(cache._dir).stat().st_mode & 0o077:
        raise ValueError('private_live_cache_directory_required')
    return cache


def session_state(session):
    return {'session_id': str(session.session_id), 'expires_at': session.expires_at.isoformat(),
            'hard_expires_at': session.hard_expires_at.isoformat()}


class PrivateLiveView(APIView):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store, private'
        return response


class LiveOperatorView(PrivateLiveView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def allowed(self, request):
        return is_nightowl_technical_user(request.user) and request.user.has_perm('agents.view_endpointperformancesample') and request.user.has_perm('agents.view_agentmachine')

    def get(self, request, endpoint_id):
        if not self.allowed(request):
            return Response({'error': 'forbidden'}, status=403)
        session = TelemetryLiveSession.objects.filter(endpoint_id=endpoint_id, expires_at__gt=timezone.now()).first()
        if not session:
            return Response({'state': 'expired', 'points': []})
        try:
            points = shared_cache().get('live:' + str(session.session_id), [])
        except Exception:
            return Response({'error': 'live_cache_unavailable'}, status=503)
        last = parse_datetime(points[-1]['collected_at']) if points else None
        return Response({**session_state(session), 'state': 'active', 'points': points,
            'last_sample_at': last.isoformat() if last else None,
            'stale': last is None or (timezone.now() - last).total_seconds() > 25})

    @transaction.atomic
    def post(self, request, endpoint_id):
        if not self.allowed(request):
            return Response({'error': 'forbidden'}, status=403)
        if not isinstance(request.data, dict) or set(request.data) - {'action', 'session_id'}:
            return Response({'error': 'invalid_live_action'}, status=400)
        endpoint = AgentMachine.objects.select_for_update().filter(pk=endpoint_id).first()
        if not endpoint or endpoint.has_terminal_lifecycle:
            return Response({'error': 'endpoint_unavailable'}, status=409)
        if request.data.get('action', 'start') not in ('start', 'renew', 'stop'):
            return Response({'error': 'invalid_action'}, status=400)
        now = timezone.now()
        session = TelemetryLiveSession.objects.filter(endpoint=endpoint).first()
        active = session and session.expires_at > now
        action = request.data.get('action', 'start')
        if active and session.operator_id != request.user.pk:
            return Response({'error': 'session_owned_by_another_operator'}, status=409)
        if action in ('renew', 'stop'):
            if not active or str(session.session_id) != str(request.data.get('session_id')):
                return Response({'error': 'session_expired'}, status=409)
            if action == 'stop':
                session.expires_at = now
                session.save(update_fields=['expires_at'])
                return Response({'state': 'expired'})
        if not active:
            if action != 'start':
                return Response({'error': 'session_expired'}, status=409)
            try:
                cache = shared_cache()
                key = 'probe:' + secrets.token_hex(8)
                cache.set(key, 'ok', 2)
                if cache.get(key) != 'ok':
                    raise ValueError('cache_unavailable')
                cache.delete(key)
            except Exception:
                return Response({'error': 'live_cache_unavailable'}, status=503)
            session, _ = TelemetryLiveSession.objects.update_or_create(endpoint=endpoint, defaults={
                'session_id': uuid.uuid4(), 'operator': request.user, 'created_at': now,
                'expires_at': now + timedelta(seconds=45), 'hard_expires_at': now + timedelta(minutes=15)})
            from .services import create_audit_event
            from .models import AuditEvent
            create_audit_event(event_type='agent.telemetry.live_started', title='Monitoramento ao vivo solicitado',
                description='Sessao temporaria, sem comando remoto.', endpoint=endpoint,
                actor_type=AuditEvent.ACTOR_USER, actor_name=request.user.get_username(),
                metadata={'session_id': str(session.session_id)})
        else:
            session.expires_at = min(now + timedelta(seconds=45), session.hard_expires_at)
            session.save(update_fields=['expires_at'])
        return Response({**session_state(session), 'state': 'active'})


class LiveAgentControlView(PrivateLiveView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        endpoint = authenticate_agent_token(request)
        session = TelemetryLiveSession.objects.filter(endpoint=endpoint, expires_at__gt=timezone.now()).first()
        return Response(session_state(session) if session else {'session_id': None})


class LiveAgentPointsView(PrivateLiveView):
    authentication_classes = []
    permission_classes = []

    @transaction.atomic
    def post(self, request):
        if len(request._request.body) > 128 * 1024:
            return Response({'error': 'payload_too_large'}, status=413)
        endpoint = authenticate_agent_token(request)
        payload = request.data
        if not isinstance(payload, dict) or set(payload) != {'session_id', 'machine_id', 'points'}:
            return Response({'error': 'invalid_live_payload'}, status=400)
        try:
            uuid.UUID(str(payload.get('session_id')))
        except (ValueError, AttributeError, TypeError):
            return Response({'error': 'invalid_live_session'}, status=400)
        session = TelemetryLiveSession.objects.select_for_update().filter(endpoint=endpoint,
            session_id=payload.get('session_id'), expires_at__gt=timezone.now()).first()
        if not session or str(payload.get('machine_id')).lower() != str(endpoint.machine_id).lower():
            return Response({'error': 'session_unavailable'}, status=403)
        try:
            points = payload['points']
            if not isinstance(points, list) or not 1 <= len(points) <= 12:
                raise ValueError()
            for point in points:
                if set(point) != {'sample_id', 'collected_at', 'metrics', 'quality'}:
                    raise ValueError()
                uuid.UUID(point['sample_id'])
                at = parse_datetime(point['collected_at'])
                if not at or timezone.is_naive(at) or not timezone.now() - timedelta(minutes=30) <= at <= timezone.now() + timedelta(seconds=30):
                    raise ValueError()
                if not isinstance(point['metrics'], dict) or len(point['metrics']) > 256:
                    raise ValueError()
                for name, value in point['metrics'].items():
                    if not isinstance(name, str) or len(name) > 220 or not name.startswith(('cpu.', 'memory.', 'disk.', 'network.', 'agent.')):
                        raise ValueError()
                    number(value)
                if not isinstance(point['quality'], dict) or len(json.dumps(point['quality'])) > 32768:
                    raise ValueError()
            cache = shared_cache()
            key = 'live:' + str(session.session_id)
            existing = cache.get(key, [])
            unique = {item['sample_id']: item for item in existing + points}
            cache.set(key, sorted(unique.values(), key=lambda item: item['collected_at'])[-180:], 90)
        except (ValueError, KeyError, TypeError):
            return Response({'error': 'invalid_live_points'}, status=400)
        except Exception:
            return Response({'error': 'live_cache_unavailable'}, status=503)
        return Response({'status': 'ok'})
