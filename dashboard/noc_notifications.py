from datetime import timedelta

from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from agents.models import AlertEvent, AlertPolicy
from .models import NocNotificationPreference, NocNotificationRead


def _policy_keys(flag):
    return list(AlertPolicy.objects.filter(enabled=True, **{flag: True}).values_list('pk', flat=True))


def _visual_filter(policies):
    return Q(alert__metadata__policy__in=policies, alert__metadata__notify_visual=True)


def _is_read(event, read_all_at):
    return event.read_individually or bool(read_all_at and event.created_at <= read_all_at)


def _entry(event, read_all_at):
    alert = event.alert
    return {
        'id': str(event.pk), 'title': alert.title, 'severity': alert.severity,
        'endpoint': alert.endpoint.hostname, 'occurred_at': event.created_at.isoformat(),
        'url': reverse('endpoint-detail', args=[alert.endpoint_id]),
        'read': _is_read(event, read_all_at),
    }


def _feed(request, events, visual_keys, read_all_at, sound, now):
    visible = events.filter(_visual_filter(visual_keys))
    unread = visible.filter(read_individually=False)
    if read_all_at:
        unread = unread.filter(created_at__gt=read_all_at)
    recent = visible.select_related('alert__endpoint')[:10]
    notices = []
    try:
        since = parse_datetime(request.GET.get('since', ''))
    except ValueError:
        since = None
    if since and timezone.is_aware(since) and since <= now:
        sound_keys = _policy_keys('notify_sound') if sound != NocNotificationPreference.Sound.OFF else []
        pending = (events.filter(created_at__gt=max(since, now - timedelta(minutes=1)))
                   .exclude(alert__status='resolved')
                   .filter(Q(alert__muted_until__isnull=True) | Q(alert__muted_until__lte=now))
                   .filter(_visual_filter(visual_keys) | Q(alert__metadata__policy__in=sound_keys,
                                                          alert__metadata__notify_sound=True))
                   .select_related('alert__endpoint')[:20])
        for event in reversed(list(pending)):
            if _is_read(event, read_all_at):
                continue
            meta = event.alert.metadata
            notices.append({
                **_entry(event, read_all_at),
                'visual': meta.get('notify_visual') is True and meta.get('policy') in visual_keys,
                'sound': meta.get('notify_sound') is True and meta.get('policy') in sound_keys
                and (sound != NocNotificationPreference.Sound.CRITICAL or event.alert.severity == 'critical'),
            })
    return JsonResponse({
        'entries': [_entry(event, read_all_at) for event in recent], 'unread': unread.count(),
        'sound': sound, 'read_at': now.isoformat(), 'notices': notices,
    })


@never_cache
@login_required
@permission_required(('agents.view_agentmachine', 'agents.view_endpointalert'), raise_exception=True)
@require_http_methods(['GET', 'POST'])
def noc_notifications(request):
    operator = request.user
    now = timezone.now()
    events = AlertEvent.objects.filter(
        event_type__in=(AlertEvent.TYPE_CREATED, AlertEvent.TYPE_REOPENED), created_at__lte=now,
        alert__endpoint__is_active=True, alert__source='alert_policy',
    ).annotate(read_individually=Exists(NocNotificationRead.objects.filter(
        operator=operator, event_id=OuterRef('pk'),
    ))).order_by('-created_at', '-pk')
    visual_keys = _policy_keys('notify_visual')
    if request.method == 'GET':
        preference = NocNotificationPreference.objects.filter(operator=operator).first()
        return _feed(
            request, events, visual_keys, preference.read_all_at if preference else None,
            preference.sound if preference else NocNotificationPreference.Sound.OFF, now,
        )
    action = request.POST.get('action')
    if action == 'read':
        try:
            event = events.filter(_visual_filter(visual_keys), pk=request.POST.get('event')).first()
        except (ValueError, TypeError, ValidationError):
            event = None
        if event is None:
            return JsonResponse({'detail': 'Notificação não encontrada.'}, status=404)
        NocNotificationRead.objects.get_or_create(operator=operator, event=event)
    elif action == 'read_all':
        with transaction.atomic():
            preference, _ = NocNotificationPreference.objects.get_or_create(operator=operator)
            NocNotificationPreference.objects.filter(pk=preference.pk).filter(
                Q(read_all_at__isnull=True) | Q(read_all_at__lt=now)
            ).update(read_all_at=now)
    elif action == 'sound' and request.POST.get('sound') in NocNotificationPreference.Sound.values:
        NocNotificationPreference.objects.update_or_create(
            operator=operator, defaults={'sound': request.POST['sound']},
        )
    else:
        return JsonResponse({'detail': 'Ação de notificação inválida.'}, status=400)
    return JsonResponse({'ok': True})
