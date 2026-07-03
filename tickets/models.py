import uuid

from django.db import models
from django.db.models import Max
from django.utils import timezone

from agents.models import AgentMachine


TICKET_PRIORITY_LOW = 'low'
TICKET_PRIORITY_NORMAL = 'normal'
TICKET_PRIORITY_HIGH = 'high'
TICKET_PRIORITY_CRITICAL = 'critical'
TICKET_PRIORITY_CHOICES = [
    (TICKET_PRIORITY_LOW, 'Baixa'),
    (TICKET_PRIORITY_NORMAL, 'Normal'),
    (TICKET_PRIORITY_HIGH, 'Alta'),
    (TICKET_PRIORITY_CRITICAL, 'Critica'),
]


class TicketCategory(models.Model):
    TYPE_INCIDENT = 'incident'
    TYPE_REQUEST = 'request'
    TYPE_RMM_ALERT = 'rmm_alert'
    TYPE_GMUD = 'gmud'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)
    allowed_types = models.JSONField(blank=True, default=list)
    subcategories = models.JSONField(blank=True, default=list)
    default_priority = models.CharField(max_length=30, choices=TICKET_PRIORITY_CHOICES, blank=True, default=TICKET_PRIORITY_NORMAL)
    default_queue = models.ForeignKey('DeskQueue', null=True, blank=True, on_delete=models.SET_NULL, related_name='default_categories')
    default_sla = models.ForeignKey('DeskSLA', null=True, blank=True, on_delete=models.SET_NULL, related_name='default_categories')
    icon = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Ticket category'
        verbose_name_plural = 'Ticket categories'

    def __str__(self):
        return self.name


class DeskQueue(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    responsible = models.CharField(max_length=150, blank=True)
    members = models.JSONField(blank=True, default=list)
    business_hours = models.CharField(max_length=120, blank=True, default='Comercial')
    is_active = models.BooleanField(default=True)
    receives_tickets = models.BooleanField(default=True)
    receives_rmm = models.BooleanField(default=False)
    receives_gmud = models.BooleanField(default=False)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Desk queue'
        verbose_name_plural = 'Desk queues'

    def __str__(self):
        return self.name


class DeskSLA(models.Model):
    CALENDAR_BUSINESS = 'business_hours'
    CALENDAR_24X7 = '24x7'
    CALENDAR_CHOICES = [
        (CALENDAR_BUSINESS, 'Horario comercial'),
        (CALENDAR_24X7, '24x7'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=30, choices=TICKET_PRIORITY_CHOICES, default=TICKET_PRIORITY_NORMAL)
    first_response_minutes = models.PositiveIntegerField(default=240)
    resolution_minutes = models.PositiveIntegerField(default=1440)
    calendar_type = models.CharField(max_length=40, choices=CALENDAR_CHOICES, default=CALENDAR_BUSINESS)
    pause_on_waiting_requester = models.BooleanField(default=True)
    pause_on_waiting_supplier = models.BooleanField(default=False)
    pause_on_waiting_approval = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    queues = models.ManyToManyField(DeskQueue, blank=True, related_name='slas')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['resolution_minutes']
        verbose_name = 'Desk SLA'
        verbose_name_plural = 'Desk SLAs'

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

    PRIORITY_LOW = TICKET_PRIORITY_LOW
    PRIORITY_NORMAL = TICKET_PRIORITY_NORMAL
    PRIORITY_HIGH = TICKET_PRIORITY_HIGH
    PRIORITY_CRITICAL = TICKET_PRIORITY_CRITICAL
    PRIORITY_CHOICES = TICKET_PRIORITY_CHOICES

    SOURCE_MANUAL = 'manual'
    SOURCE_RMM_ALERT = 'rmm_alert'
    SOURCE_ENDPOINT = 'endpoint'
    SOURCE_FUTURE_AGENT_POPUP = 'future_agent_popup'
    SOURCE_EMAIL = 'email'
    SOURCE_PORTAL = 'portal'
    SOURCE_PHONE = 'phone'
    SOURCE_MONITORING = 'monitoring'
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, 'Manual'),
        (SOURCE_RMM_ALERT, 'RMM / Alerta'),
        (SOURCE_ENDPOINT, 'Endpoint'),
        (SOURCE_FUTURE_AGENT_POPUP, 'Popup futuro do agente'),
        (SOURCE_EMAIL, 'E-mail'),
        (SOURCE_PORTAL, 'Portal'),
        (SOURCE_PHONE, 'Telefone'),
        (SOURCE_MONITORING, 'Monitoramento'),
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
    queue = models.CharField(max_length=150, blank=True, default='N1 - Atendimento')
    assigned_to = models.CharField(max_length=150, blank=True)
    endpoint = models.ForeignKey(AgentMachine, null=True, blank=True, on_delete=models.SET_NULL, related_name='tickets')
    endpoint_name = models.CharField(max_length=150, blank=True)
    sla = models.ForeignKey(DeskSLA, null=True, blank=True, on_delete=models.SET_NULL, related_name='tickets')
    due_at = models.DateTimeField(null=True, blank=True)
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
        needs_due_at = bool(self.sla and not self.due_at)
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

        if needs_due_at:
            self.due_at = self.created_at + timezone.timedelta(minutes=self.sla.resolution_minutes)
            super().save(update_fields=['due_at'])

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


class TicketAttachment(models.Model):
    VISIBILITY_INTERNAL = 'internal'
    VISIBILITY_PUBLIC = 'public'
    VISIBILITY_CHOICES = [
        (VISIBILITY_INTERNAL, 'Interno'),
        (VISIBILITY_PUBLIC, 'Publico'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, related_name='attachments', on_delete=models.CASCADE)
    comment = models.ForeignKey(TicketComment, related_name='attachments', null=True, blank=True, on_delete=models.SET_NULL)
    file = models.FileField(upload_to='tickets/attachments/%Y/%m/')
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    uploaded_by = models.CharField(max_length=150, blank=True)
    visibility = models.CharField(max_length=30, choices=VISIBILITY_CHOICES, default=VISIBILITY_PUBLIC)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['ticket', 'visibility', '-created_at']),
            models.Index(fields=['comment', '-created_at']),
        ]

    def __str__(self):
        return f'{self.original_name} - #{self.ticket.number}'

    @property
    def is_public(self):
        return self.visibility == self.VISIBILITY_PUBLIC


class TicketAuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, related_name='audit_events', on_delete=models.CASCADE)
    actor = models.CharField(max_length=150, blank=True, default='Sistema')
    event_type = models.CharField(max_length=60)
    action = models.CharField(max_length=200)
    field_name = models.CharField(max_length=100, blank=True)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    metadata = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['ticket', '-created_at']),
            models.Index(fields=['event_type', '-created_at']),
        ]

    def __str__(self):
        return f'Auditoria #{self.ticket.number}: {self.action}'


class DeskTemplate(models.Model):
    TYPE_OPENING = 'opening'
    TYPE_PUBLIC_REPLY = 'public_reply'
    TYPE_INTERNAL_COMMENT = 'internal_comment'
    TYPE_AUTOMATIC_REPLY = 'automatic_reply'
    TYPE_RESOLUTION = 'resolution'
    TYPE_ESCALATION = 'escalation'
    TYPE_GMUD = 'gmud'
    TYPE_CHECKLIST = 'checklist'
    TYPE_CHOICES = [
        (TYPE_OPENING, 'Abertura'),
        (TYPE_PUBLIC_REPLY, 'Resposta publica'),
        (TYPE_INTERNAL_COMMENT, 'Comentario interno'),
        (TYPE_AUTOMATIC_REPLY, 'Resposta automatica'),
        (TYPE_RESOLUTION, 'Resolucao'),
        (TYPE_ESCALATION, 'Escalacao'),
        (TYPE_GMUD, 'GMUD'),
        (TYPE_CHECKLIST, 'Checklist'),
    ]
    APP_COMPOSER_PUBLIC = 'composer_publico'
    APP_COMPOSER_INTERNAL = 'composer_interno'
    APP_RESOLVE_TICKET = 'resolver_chamado'
    APP_ESCALATE_TICKET = 'escalar_chamado'
    APP_TICKET_CREATED = 'automacao_chamado_criado'
    APP_TICKET_RESOLVED = 'automacao_chamado_resolvido'
    APP_TICKET_REOPENED = 'automacao_chamado_reaberto'
    APP_WAITING_REQUESTER = 'automacao_aguardando_solicitante'
    APPLICATION_CHOICES = [
        (APP_COMPOSER_PUBLIC, 'Composer publico'),
        (APP_COMPOSER_INTERNAL, 'Composer interno'),
        (APP_RESOLVE_TICKET, 'Resolver chamado'),
        (APP_ESCALATE_TICKET, 'Escalar chamado'),
        (APP_TICKET_CREATED, 'Automacao: chamado criado'),
        (APP_TICKET_RESOLVED, 'Automacao: chamado resolvido'),
        (APP_TICKET_REOPENED, 'Automacao: chamado reaberto'),
        (APP_WAITING_REQUESTER, 'Automacao: aguardando solicitante'),
    ]
    CHANNEL_INTERNAL = 'internal'
    CHANNEL_PUBLIC = 'public'
    CHANNEL_AUTOMATIC = 'automatic'
    CHANNEL_APPROVAL = 'approval'
    CHANNEL_CHOICES = [
        (CHANNEL_INTERNAL, 'Interno'),
        (CHANNEL_PUBLIC, 'Publico'),
        (CHANNEL_AUTOMATIC, 'Automatico'),
        (CHANNEL_APPROVAL, 'Aprovacao'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    template_type = models.CharField(max_length=40, choices=TYPE_CHOICES, default=TYPE_PUBLIC_REPLY)
    application = models.CharField(max_length=120, choices=APPLICATION_CHOICES, blank=True)
    category = models.ForeignKey(TicketCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='templates')
    channel = models.CharField(max_length=40, choices=CHANNEL_CHOICES, default=CHANNEL_PUBLIC)
    subject = models.CharField(max_length=200, blank=True)
    trigger = models.CharField(max_length=120, blank=True)
    content = models.TextField()
    variables = models.JSONField(blank=True, default=list)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['template_type', 'name']
        verbose_name = 'Desk template'
        verbose_name_plural = 'Desk templates'

    def __str__(self):
        return self.name


class NotificationOutbox(models.Model):
    SOURCE_DESK = 'desk'
    SOURCE_RMM = 'rmm'
    SOURCE_SYSTEM = 'system'
    SOURCE_GMUD = 'gmud'
    SOURCE_AUTH = 'auth'
    SOURCE_CHOICES = [
        (SOURCE_DESK, 'Desk'),
        (SOURCE_RMM, 'RMM'),
        (SOURCE_SYSTEM, 'Sistema'),
        (SOURCE_GMUD, 'GMUD'),
        (SOURCE_AUTH, 'Autenticacao'),
    ]
    CHANNEL_EMAIL = 'email'
    CHANNEL_CHOICES = [
        (CHANNEL_EMAIL, 'E-mail'),
    ]
    STATUS_PENDING = 'pending'
    STATUS_SENDING = 'sending'
    STATUS_SENT = 'sent'
    STATUS_SKIPPED = 'skipped'
    STATUS_FAILED = 'failed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pendente'),
        (STATUS_SENDING, 'Enviando'),
        (STATUS_SENT, 'Enviada'),
        (STATUS_SKIPPED, 'Ignorada'),
        (STATUS_FAILED, 'Falhou'),
        (STATUS_CANCELLED, 'Cancelada'),
    ]
    PRIORITY_LOW = 'low'
    PRIORITY_NORMAL = 'normal'
    PRIORITY_HIGH = 'high'
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, 'Baixa'),
        (PRIORITY_NORMAL, 'Normal'),
        (PRIORITY_HIGH, 'Alta'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_app = models.CharField(max_length=30, choices=SOURCE_CHOICES, default=SOURCE_SYSTEM)
    source_model = models.CharField(max_length=120, blank=True)
    source_id = models.CharField(max_length=120, blank=True)
    ticket = models.ForeignKey(
        Ticket,
        related_name='notification_outbox',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    template = models.ForeignKey(
        DeskTemplate,
        related_name='notification_outbox',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    event_type = models.CharField(max_length=80)
    channel = models.CharField(max_length=30, choices=CHANNEL_CHOICES, default=CHANNEL_EMAIL)
    recipient_name = models.CharField(max_length=150, blank=True)
    recipient_email = models.EmailField(blank=True)
    cc = models.JSONField(default=list, blank=True)
    bcc = models.JSONField(default=list, blank=True)
    subject = models.CharField(max_length=250, blank=True)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    last_error = models.TextField(blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['source_app', 'created_at']),
            models.Index(fields=['priority', 'created_at']),
            models.Index(fields=['ticket', '-created_at']),
            models.Index(fields=['event_type', '-created_at']),
        ]
        verbose_name = 'Notification outbox'
        verbose_name_plural = 'Notification outbox'

    def __str__(self):
        reference = f'#{self.ticket.number}' if self.ticket else self.source_id or self.source_app
        return f'{self.event_type} - {reference} - {self.status}'
