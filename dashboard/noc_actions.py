from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST

from agents.models import AgentJob, AgentMachine, EndpointAlert
from config.authz import is_nightowl_technical_user
from tickets.models import Ticket
from tickets.services.desk_mvp1 import create_audit_event
from tickets.services.ticket_alert_mapping import priority_for_alert

from . import views


VERIFICATION_TYPES = {
    'disk_low': AgentJob.TYPE_COLLECT_DISKS,
    'security_antivirus': AgentJob.TYPE_COLLECT_SECURITY,
    'security_alternative_av': AgentJob.TYPE_COLLECT_SECURITY,
    'stale_inventory': AgentJob.TYPE_FORCE_INVENTORY,
    'high_uptime': AgentJob.TYPE_FORCE_INVENTORY,
    'endpoint_offline': AgentJob.TYPE_PING,
}


@login_required
@permission_required(('agents.view_agentmachine', 'agents.view_endpointalert'), raise_exception=True)
@require_POST
@transaction.atomic
def noc_alert_action(request, pk, action):
    # Match the evaluator's lock order before changing a shared occurrence.
    endpoint_id = get_object_or_404(
        EndpointAlert.objects.only('endpoint_id'), pk=pk, endpoint__is_active=True,
    ).endpoint_id
    endpoint = get_object_or_404(
        AgentMachine.objects.select_for_update(), pk=endpoint_id, is_active=True,
    )
    alert = get_object_or_404(
        EndpointAlert.objects.select_for_update(),
        pk=pk, endpoint=endpoint,
    )
    if action in ('acknowledge', 'mute'):
        if not request.user.has_perm('agents.change_endpointalert'):
            return JsonResponse({'detail': 'Você não tem permissão para alterar alertas.'}, status=403)
        if alert.status == EndpointAlert.STATUS_RESOLVED:
            return JsonResponse({'detail': 'O alerta já foi resolvido.'}, status=409)
        if action == 'acknowledge':
            return views.alert_acknowledge(request, alert.pk, noc=True)
        return views.alert_mute(request, alert.pk, noc=True)

    if not is_nightowl_technical_user(request.user):
        return JsonResponse({'detail': 'Acesso técnico necessário.'}, status=403)
    if action == 'verify':
        if not request.user.has_perm('agents.add_agentjob'):
            return JsonResponse({'detail': 'Você não tem permissão para enfileirar verificações.'}, status=403)
        if alert.status == EndpointAlert.STATUS_RESOLVED:
            return JsonResponse({'detail': 'O alerta já foi resolvido.'}, status=409)
        job_type = VERIFICATION_TYPES.get(alert.alert_type)
        if not job_type:
            return JsonResponse({'detail': 'Não há verificação compatível com este alerta.'}, status=409)
        return views.endpoint_job_create(request, alert.endpoint_id, noc_alert=alert, noc_job_type=job_type)
    if action != 'ticket':
        return JsonResponse({'detail': 'Ação desconhecida.'}, status=404)

    if not request.user.has_perm('tickets.add_ticket'):
        return JsonResponse({'detail': 'Você não tem permissão para abrir chamados.'}, status=403)
    ticket = Ticket.objects.filter(related_alert_id=str(alert.pk)).order_by('pk').first()
    if ticket:
        return _ticket_response(ticket, created=False)
    if alert.status == EndpointAlert.STATUS_RESOLVED:
        return JsonResponse({'detail': 'O alerta já foi resolvido.'}, status=409)
    ticket = Ticket.objects.create(
        title=alert.title[:200], description=alert.description, endpoint=endpoint,
        endpoint_name=endpoint.hostname[:150], priority=priority_for_alert(alert.severity),
        requester_username=request.user.get_username()[:150],
        requester_name=(request.user.get_full_name() or request.user.get_username())[:150],
        source=Ticket.SOURCE_RMM_ALERT, related_alert_id=str(alert.pk),
    )
    create_audit_event(
        ticket, actor=request.user.get_username(), event_type='ticket_created',
        action='Criou chamado pelo NOC', metadata={'alert_id': str(alert.pk)},
    )
    return _ticket_response(ticket, created=True)


def _ticket_response(ticket, *, created):
    prefix = 'Chamado criado' if created else 'Chamado já vinculado'
    return JsonResponse({
        'detail': f'{prefix} #{ticket.number}.', 'url': reverse('tickets:detail', args=[ticket.number]),
    }, status=201 if created else 200)
