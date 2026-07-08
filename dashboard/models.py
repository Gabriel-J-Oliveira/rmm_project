import uuid

from django.db import models


class OperationalTask(models.Model):
    STATUS_PLANNED = 'planned'
    STATUS_OPEN = 'open'
    STATUS_SCHEDULED = 'scheduled'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_WAITING = 'waiting'
    STATUS_COMPLETED = 'completed'
    STATUS_DONE = 'done'
    STATUS_CANCELLED = 'cancelled'
    STATUS_RESOLVED = 'resolved'
    STATUS_CHOICES = [
        (STATUS_PLANNED, 'Planejada'),
        (STATUS_OPEN, 'Aberta'),
        (STATUS_SCHEDULED, 'Agendada'),
        (STATUS_IN_PROGRESS, 'Em andamento'),
        (STATUS_WAITING, 'Aguardando'),
        (STATUS_COMPLETED, 'Concluida'),
        (STATUS_DONE, 'Concluida'),
        (STATUS_CANCELLED, 'Cancelada'),
        (STATUS_RESOLVED, 'Resolvida'),
    ]
    ACTIVE_STATUSES = {
        STATUS_PLANNED,
        STATUS_OPEN,
        STATUS_SCHEDULED,
        STATUS_IN_PROGRESS,
        STATUS_WAITING,
    }

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

    CATEGORY_SUPPORT = 'support'
    CATEGORY_MAINTENANCE = 'maintenance'
    CATEGORY_SECURITY = 'security'
    CATEGORY_INVENTORY = 'inventory'
    CATEGORY_ONBOARDING = 'onboarding'
    CATEGORY_OFFBOARDING = 'offboarding'
    CATEGORY_CHANGE = 'change'
    CATEGORY_CHOICES = [
        (CATEGORY_SUPPORT, 'Suporte'),
        (CATEGORY_MAINTENANCE, 'Manutencao'),
        (CATEGORY_SECURITY, 'Seguranca'),
        (CATEGORY_INVENTORY, 'Inventario'),
        (CATEGORY_ONBOARDING, 'Onboarding'),
        (CATEGORY_OFFBOARDING, 'Offboarding'),
        (CATEGORY_CHANGE, 'Mudanca'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=220)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_OPEN)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL)
    category = models.CharField(max_length=40, choices=CATEGORY_CHOICES, default=CATEGORY_SUPPORT)
    start_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    responsible = models.CharField(max_length=160, blank=True)
    linked_ticket = models.ForeignKey(
        'tickets.Ticket',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='operational_tasks',
    )
    linked_ticket_ref = models.CharField(max_length=80, blank=True)
    linked_endpoint = models.ForeignKey(
        'agents.AgentMachine',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='operational_tasks',
    )
    linked_endpoint_name = models.CharField(max_length=180, blank=True)
    linked_user = models.CharField(max_length=180, blank=True)
    location = models.CharField(max_length=180, blank=True)
    checklist = models.JSONField(blank=True, default=list)
    job_ids = models.JSONField(blank=True, default=list)
    timeline = models.JSONField(blank=True, default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['due_at', '-updated_at']
        indexes = [
            models.Index(fields=['status', 'due_at']),
            models.Index(fields=['due_at']),
            models.Index(fields=['responsible']),
        ]

    def __str__(self):
        return self.title

    @property
    def is_active_for_reminders(self):
        return bool(self.due_at and self.status in self.ACTIVE_STATUSES)

    def checklist_progress(self):
        items = self.checklist or []
        total = len(items)
        done = sum(1 for item in items if isinstance(item, dict) and item.get('done'))
        return done, total

    def add_timeline_event(self, text, *, actor='Sistema', event_type='task.updated', at=None):
        from django.utils import timezone

        event = {
            'at': (at or timezone.now()).isoformat(),
            'actor': actor,
            'event_type': event_type,
            'text': text,
        }
        self.timeline = [*(self.timeline or []), event]
        self.save(update_fields=['timeline', 'updated_at'])
        return event


class TaskReminderLog(models.Model):
    REMINDER_7D = '7d'
    REMINDER_3D = '3d'
    REMINDER_8H = '8h'
    REMINDER_TYPE_CHOICES = [
        (REMINDER_7D, '7 dias'),
        (REMINDER_3D, '3 dias'),
        (REMINDER_8H, '8 horas'),
    ]

    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_SENT, 'Enviado'),
        (STATUS_FAILED, 'Falhou'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(OperationalTask, related_name='reminder_logs', on_delete=models.CASCADE)
    reminder_type = models.CharField(max_length=8, choices=REMINDER_TYPE_CHOICES)
    sent_to = models.EmailField()
    sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['task', 'reminder_type', 'sent_to'],
                name='unique_task_reminder_recipient',
            ),
        ]
        indexes = [
            models.Index(fields=['reminder_type', 'status']),
            models.Index(fields=['sent_to']),
        ]

    def __str__(self):
        return f'{self.task} - {self.reminder_type} - {self.sent_to}'
