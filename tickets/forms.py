from django import forms

from agents.models import AgentMachine
from .models import Ticket, TicketComment


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = [
            'title',
            'description',
            'category',
            'priority',
            'requester_name',
            'requester_email',
            'requester_username',
            'requester_department',
            'requester_role',
            'requester_is_partner',
            'endpoint',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 6}),
            'requester_is_partner': forms.CheckboxInput(attrs={'class': 'ticket-checkbox-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = self.fields['category'].queryset.filter(is_active=True)
        self.fields['category'].required = False
        self.fields['endpoint'].queryset = AgentMachine.objects.order_by('hostname')
        self.fields['endpoint'].required = False
        for field in self.fields.values():
            css_class = 'ticket-input'
            existing = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = f'{existing} {css_class}'.strip()


class TicketStatusPriorityForm(forms.Form):
    status = forms.ChoiceField(choices=Ticket.STATUS_CHOICES)
    priority = forms.ChoiceField(choices=Ticket.PRIORITY_CHOICES)


class TicketAssignForm(forms.Form):
    assigned_to = forms.CharField(max_length=150, required=False)


class TicketCommentForm(forms.ModelForm):
    class Meta:
        model = TicketComment
        fields = ['body', 'visibility']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Adicionar comentario interno...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'ticket-input'
