from django import forms

from agents.models import AgentMachine, AlertPolicy


METRIC_OPTIONS = {
    'cpu': {'unit': '%', 'max': 100, 'coverage': True},
    'ram': {'unit': '%', 'max': 100, 'coverage': True},
    'disk': {'unit': '%', 'max': 100},
    'offline': {'unit': 'h', 'no_freshness': True},
    'uptime': {'unit': 'dias'},
    'antivirus': {'binary': True},
    'job_failures': {'unit': 'falhas', 'window': True},
}


class AlertPolicyForm(forms.ModelForm):
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=0)

    class Meta:
        model = AlertPolicy
        fields = (
            'name', 'enabled', 'metric', 'severity', 'threshold', 'duration_minutes', 'recovery_threshold',
            'antivirus_condition', 'job_window_minutes', 'max_age_minutes', 'min_coverage_percent',
            'scope', 'endpoints', 'notify_visual', 'notify_sound',
        )
        labels = {
            'name': 'Nome', 'enabled': 'Ativa', 'metric': 'Métrica', 'severity': 'Criticidade', 'threshold': 'Limite',
            'duration_minutes': 'Duração mínima (min)', 'recovery_threshold': 'Limite de recuperação',
            'antivirus_condition': 'Condição do antivírus', 'job_window_minutes': 'Janela de falhas (min)',
            'max_age_minutes': 'Idade máxima do dado (min)', 'min_coverage_percent': 'Cobertura mínima (%)',
            'scope': 'Escopo', 'endpoints': 'Endpoints', 'notify_visual': 'Notificação visual',
            'notify_sound': 'Notificação sonora',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['revision'] = self.instance.revision
        self.fields['enabled'].widget.attrs['role'] = 'switch'
        self.fields['endpoints'].queryset = AgentMachine.objects.filter(is_active=True).order_by('hostname', 'pk')
        self.fields['duration_minutes'].help_text = 'Use 0 para avaliar sem espera adicional.'

    def clean(self):
        data = super().clean()
        metric = METRIC_OPTIONS.get(data.get('metric'))
        if metric is None:
            return data
        enabled = data.get('enabled')
        for field, applicable in (
            ('antivirus_condition', metric.get('binary')),
            ('job_window_minutes', metric.get('window')),
            ('max_age_minutes', not metric.get('no_freshness')),
            ('min_coverage_percent', metric.get('coverage')),
        ):
            if not applicable:
                data[field] = '' if field == 'antivirus_condition' else None
            elif enabled and not data.get(field) and field not in self.errors:
                self.add_error(field, 'Preencha este parâmetro para ativar a política.')
        if metric.get('binary'):
            data.update(threshold=None, recovery_threshold=None)
        else:
            for field in ('threshold', 'recovery_threshold'):
                value = data.get(field)
                if enabled and value is None and field not in self.errors:
                    self.add_error(field, 'Preencha este limite para ativar a política.')
                elif value is not None and 'max' in metric and value > metric['max']:
                    self.add_error(field, 'Informe uma porcentagem entre 0 e 100.')
            threshold = data.get('threshold')
            recovery = data.get('recovery_threshold')
            if threshold is not None and threshold < 1:
                self.add_error('threshold', 'Informe um limite maior que zero.')
            elif threshold is not None and recovery is not None and recovery >= threshold:
                self.add_error('recovery_threshold', 'A recuperação deve ocorrer abaixo do limite de disparo.')
        if data.get('scope') == AlertPolicy.Scope.ENDPOINTS:
            if not data.get('endpoints') and 'endpoints' not in self.errors:
                self.add_error('endpoints', 'Selecione pelo menos um endpoint.')
        elif 'endpoints' in data:
            data['endpoints'] = AgentMachine.objects.none()
        return data
