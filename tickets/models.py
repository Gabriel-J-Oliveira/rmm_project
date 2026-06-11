import uuid

from django.db import models
from django.db.models import Max
from django.utils import timezone

from agents.models import AgentMachine


class TicketCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Ticket category'
        verbose_name_plural = 'Ticket categories'

    def __str__(self):
        return self.name


class Ticket(models.Model):
    STATUS_NEW = 'new'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_WAITING_USER = 'waiting_user'
    STATUS_WAITING_THIRD_PARTY = 'waiting_third_party'
    STATUS_RESOLVED = 'resolved'
    STATUS_CLOSED = 'closed'
    STATUS_CANCELED = 'canceled'
    STATUS_CHOICES = [
        (STATUS_NEW, 'Novo'),
        (STATUS_IN_PROGRESS, 'Em atendimento'),
        (STATUS_WAITING_USER, 'Aguardando usuario'),
        (STATUS_WAITING_THIRD_PARTY, 'Aguardando terceiro'),
        (STATUS_RESOLVED, 'Resolvido'),
        (STATUS_CLOSED, 'Fechado'),
        (STATUS_CANCELED, 'Cancelado'),
    ]

    PRIORITY_LOW = 'low'
    PRIORITY_NORMAL = 'normal'
    PRIORITY_HIGH = 'high'
    PRIORITY_CRITICAL = 'critical'
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, 'Baixa'),
        (PRIORITY_NORMAL, 'Normal'),
        (PRIORITY_HIGH, 'Alta'),
        (PRIORITY_CRITICAL, 'Critica'),
    ]

    SOURCE_MANUAL = 'manual'
    SOURCE_RMM_ALERT = 'rmm_alert'
    SOURCE_ENDPOINT = 'endpoint'
    SOURCE_FUTURE_AGENT_POPUP = 'future_agent_popup'
    SOURCE_EMAIL = 'email'
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, 'Manual'),
        (SOURCE_RMM_ALERT, 'RMM / Alerta'),
        (SOURCE_ENDPOINT, 'Endpoint'),
        (SOURCE_FUTURE_AGENT_POPUP, 'Popup futuro do agente'),
        (SOURCE_EMAIL, 'E-mail'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.PositiveIntegerField(unique=True, editable=False)
    title = models.CharField(max_length=200)
    description = models.TextField()
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default=STATUS_NEW)
    priority = models.CharField(max_length=30, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL)
    category = models.ForeignKey(TicketCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='tickets')
    requester_name = models.CharField(max_length=150, blank=True)
    requester_email = models.EmailField(blank=True)
    requester_username = models.CharField(max_length=150, blank=True)
    requester_department = models.CharField(max_length=150, blank=True)
    requester_role = models.CharField(max_length=150, blank=True)
    requester_is_partner = models.BooleanField(default=False)
    assigned_to = models.CharField(max_length=150, blank=True)
    endpoint = models.ForeignKey(AgentMachine, null=True, blank=True, on_delete=models.SET_NULL, related_name='tickets')
    source = models.CharField(max_length=40, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    related_alert_id = models.CharField(max_length=120, blank=True)
    related_policy_id = models.CharField(max_length=120, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['status', '-updated_at']),
            models.Index(fields=['priority', '-updated_at']),
            models.Index(fields=['assigned_to']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        number = self.number or 'novo'
        return f'#{number} - {self.title}'

    def save(self, *args, **kwargs):
        now = timezone.now()
        if not self.number:
            last_number = Ticket.objects.aggregate(max_number=Max('number'))['max_number'] or 0
            self.number = last_number + 1

        if self.requester_is_partner:
            self.priority = self.PRIORITY_CRITICAL

        if self.assigned_to and not self.assigned_at:
            self.assigned_at = now
        if self.assigned_to and not self.first_response_at:
            self.first_response_at = now
        if self.status == self.STATUS_RESOLVED and not self.resolved_at:
            self.resolved_at = now
        if self.status == self.STATUS_CLOSED and not self.closed_at:
            self.closed_at = now

        super().save(*args, **kwargs)

    @property
    def is_open(self):
        return self.status not in {self.STATUS_RESOLVED, self.STATUS_CLOSED, self.STATUS_CANCELED}


class TicketComment(models.Model):
    VISIBILITY_INTERNAL = 'internal'
    VISIBILITY_PUBLIC = 'public'
    VISIBILITY_CHOICES = [
        (VISIBILITY_INTERNAL, 'Interno'),
        (VISIBILITY_PUBLIC, 'Publico'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, related_name='comments', on_delete=models.CASCADE)
    author_name = models.CharField(max_length=150, blank=True)
    body = models.TextField()
    visibility = models.CharField(max_length=30, choices=VISIBILITY_CHOICES, default=VISIBILITY_INTERNAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'Comentario #{self.ticket.number}'
