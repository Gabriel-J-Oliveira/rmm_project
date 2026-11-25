from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from agents.models import AlertPolicy, AuditEvent
from .alert_policy_forms import AlertPolicyForm, METRIC_OPTIONS


def _audit_snapshot(policy):
    return {
        **{name: getattr(policy, name) for name in AlertPolicyForm.Meta.fields if name != 'endpoints'},
        'endpoints': sorted(str(pk) for pk in policy.endpoints.values_list('pk', flat=True)),
        'revision': policy.revision,
    }


@login_required
@require_http_methods(['GET'])
def alert_policies(request):
    can_edit = request.user.has_perm('agents.change_alertpolicy')
    if not can_edit and not request.user.has_perm('agents.view_alertpolicy'):
        raise PermissionDenied
    return render(request, 'dashboard/alert_policies.html', {
        'active_nav': 'alert_policies',
        'can_edit': can_edit,
        'policies': AlertPolicy.objects.prefetch_related('endpoints'),
    })


@login_required
@permission_required('agents.change_alertpolicy', raise_exception=True)
@require_http_methods(['GET', 'POST'])
def edit_alert_policy(request, key):
    if request.method == 'POST':
        with transaction.atomic():
            policy = get_object_or_404(AlertPolicy.objects.select_for_update(), pk=key)
            before = _audit_snapshot(policy)
            form = AlertPolicyForm(request.POST, instance=policy)
            if form.is_valid() and form.cleaned_data['revision'] != before['revision']:
                form.add_error(None, 'Esta política foi alterada. Reabra a edição antes de salvar.')
            if not form.errors:
                policy.revision += 1
                form.save()
                AuditEvent.objects.create(
                    event_type='alert_policy.updated', actor_type=AuditEvent.ACTOR_USER,
                    actor_name=request.user.get_username(), title='Política de alertas atualizada',
                    metadata={'policy': key, 'before': before, 'after': _audit_snapshot(policy)},
                )
                messages.success(request, 'Política de alertas salva.')
                return redirect('alert-policies')
    else:
        policy = get_object_or_404(AlertPolicy, pk=key)
        form = AlertPolicyForm(instance=policy)
    return render(request, 'dashboard/alert_policies.html', {
        'active_nav': 'alert_policies', 'policy': policy, 'form': form, 'metric_options': METRIC_OPTIONS,
    }, status=422 if request.method == 'POST' else 200)
