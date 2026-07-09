import hashlib
import hmac
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def hash_agent_token(token: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        token.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def generate_enrollment_token() -> str:
    return f'enroll_{secrets.token_urlsafe(32)}'


def hash_enrollment_token(token: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        token.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def generate_manual_validation_token() -> str:
    return f'manual_{secrets.token_hex(5).upper()}'


def hash_manual_validation_token(token: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        token.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


class AgentMachine(models.Model):
    STATUS_ONLINE = 'online'
    STATUS_OFFLINE = 'offline'
    STATUS_UNKNOWN = 'unknown'
    STATUS_CHOICES = [
        (STATUS_ONLINE, 'Online'),
        (STATUS_OFFLINE, 'Offline'),
        (STATUS_UNKNOWN, 'Unknown'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    machine_id = models.CharField(max_length=255, blank=True, db_index=True)
    hostname = models.CharField(max_length=255)
    domain = models.CharField(max_length=255, blank=True)
    fqdn = models.CharField(max_length=512, blank=True)
    agent_token_hash = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_UNKNOWN,
    )
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    last_logged_user = models.CharField(max_length=255, blank=True)
    os_name = models.CharField(max_length=255, blank=True)
    os_version = models.CharField(max_length=128, blank=True)
    windows_build = models.CharField(max_length=128, blank=True)
    manufacturer = models.CharField(max_length=255, blank=True)
    model = models.CharField(max_length=255, blank=True)
    serial_number = models.CharField(max_length=255, blank=True)
    agent_version = models.CharField(max_length=50, blank=True)
    agent_mode = models.CharField(max_length=50, blank=True)
    agent_install_path = models.CharField(max_length=255, blank=True)
    agent_task_name = models.CharField(max_length=120, blank=True)
    agent_runtime = models.CharField(max_length=80, blank=True)
    agent_runtime_version = models.CharField(max_length=80, blank=True)
    agent_update_source = models.CharField(max_length=500, blank=True)
    agent_reported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['hostname', 'domain']
        indexes = [
            models.Index(fields=['hostname', 'domain']),
            models.Index(fields=['last_seen_at']),
        ]

    def __str__(self) -> str:
        return self.fqdn or self.hostname

    @property
    def is_authenticated(self) -> bool:
        return True

    @classmethod
    def generate_token(cls) -> str:
        return f'rmm_live_{secrets.token_urlsafe(32)}'

    def set_agent_token(self, token: str) -> None:
        self.agent_token_hash = hash_agent_token(token)

    @classmethod
    def create_with_token(cls, **kwargs):
        token = cls.generate_token()
        machine = cls(**kwargs)
        machine.set_agent_token(token)
        machine.save()
        return machine, token

    def mark_seen(self, seen_at=None) -> None:
        now = seen_at or timezone.now()
        if self.first_seen_at is None:
            self.first_seen_at = now
        self.last_seen_at = now


class AgentEnrollmentToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    token_hash = models.CharField(max_length=128, unique=True)
    prefix = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0)
    allowed_domain = models.CharField(max_length=150, blank=True)
    notes = models.TextField(blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['is_active', 'expires_at']),
            models.Index(fields=['allowed_domain']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.name} ({self.prefix or "sem prefixo"})'

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())

    @property
    def usage_limit_reached(self) -> bool:
        return bool(self.max_uses is not None and self.used_count >= self.max_uses)

    def can_be_used(self) -> bool:
        return self.is_active and not self.is_expired and not self.usage_limit_reached

    def mark_used(self) -> None:
        self.used_count += 1
        self.last_used_at = timezone.now()
        self.save(update_fields=['used_count', 'last_used_at', 'updated_at'])

    @classmethod
    def create_with_token(cls, **kwargs):
        token = generate_enrollment_token()
        instance = cls(
            token_hash=hash_enrollment_token(token),
            prefix=token[:18],
            **kwargs,
        )
        instance.save()
        return instance, token


class AgentEnrollmentLog(models.Model):
    STATUS_SUCCESS = 'success'
    STATUS_DENIED = 'denied'
    STATUS_EXPIRED = 'expired'
    STATUS_INACTIVE = 'inactive'
    STATUS_USAGE_LIMIT_REACHED = 'usage_limit_reached'
    STATUS_INVALID_TOKEN = 'invalid_token'
    STATUS_DOMAIN_DENIED = 'domain_denied'
    STATUS_MANUAL_VALIDATION_REQUIRED = 'manual_validation_required'
    STATUS_INVALID_MANUAL_VALIDATION_TOKEN = 'invalid_manual_validation_token'
    STATUS_MANUAL_VALIDATION_TOKEN_EXPIRED = 'manual_validation_token_expired'
    STATUS_MANUAL_VALIDATION_TOKEN_USED = 'manual_validation_token_used'
    STATUS_ERROR = 'error'
    STATUS_CHOICES = [
        (STATUS_SUCCESS, 'Success'),
        (STATUS_DENIED, 'Denied'),
        (STATUS_EXPIRED, 'Expired'),
        (STATUS_INACTIVE, 'Inactive'),
        (STATUS_USAGE_LIMIT_REACHED, 'Usage limit reached'),
        (STATUS_INVALID_TOKEN, 'Invalid token'),
        (STATUS_DOMAIN_DENIED, 'Domain denied'),
        (STATUS_MANUAL_VALIDATION_REQUIRED, 'Manual validation required'),
        (STATUS_INVALID_MANUAL_VALIDATION_TOKEN, 'Invalid manual validation token'),
        (STATUS_MANUAL_VALIDATION_TOKEN_EXPIRED, 'Manual validation token expired'),
        (STATUS_MANUAL_VALIDATION_TOKEN_USED, 'Manual validation token used'),
        (STATUS_ERROR, 'Error'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    enrollment_token = models.ForeignKey(
        AgentEnrollmentToken,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs',
    )
    endpoint = models.ForeignKey(
        AgentMachine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='enrollment_logs',
    )
    hostname = models.CharField(max_length=150)
    domain = models.CharField(max_length=150, blank=True)
    serial_number = models.CharField(max_length=150, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    status = models.CharField(max_length=40, choices=STATUS_CHOICES)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['hostname']),
            models.Index(fields=['domain']),
        ]

    def __str__(self) -> str:
        return f'{self.status} - {self.hostname}'


class AgentManualValidationToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_hash = models.CharField(max_length=128, unique=True)
    prefix = models.CharField(max_length=30, blank=True)
    name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    used_by_hostname = models.CharField(max_length=150, blank=True)
    used_by_domain = models.CharField(max_length=150, blank=True)
    enrollment_token = models.ForeignKey(
        AgentEnrollmentToken,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='manual_validation_tokens',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['is_active', 'expires_at']),
            models.Index(fields=['used_at']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self) -> str:
        return self.prefix or str(self.id)

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    def can_be_used(self) -> bool:
        return self.is_active and not self.is_expired and not self.is_used

    def mark_used(self, hostname: str, domain: str) -> None:
        self.used_at = timezone.now()
        self.used_by_hostname = hostname
        self.used_by_domain = domain
        self.save(update_fields=['used_at', 'used_by_hostname', 'used_by_domain'])

    @classmethod
    def create_with_token(cls, **kwargs):
        token = generate_manual_validation_token()
        instance = cls(
            token_hash=hash_manual_validation_token(token),
            prefix=token[:18],
            **kwargs,
        )
        instance.save()
        return instance, token


class InventorySnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    machine = models.ForeignKey(
        AgentMachine,
        on_delete=models.CASCADE,
        related_name='inventory_snapshots',
    )
    collected_at = models.DateTimeField()
    received_at = models.DateTimeField(default=timezone.now)
    hostname = models.CharField(max_length=255)
    domain = models.CharField(max_length=255, blank=True)
    logged_user = models.CharField(max_length=255, blank=True)
    ips = models.JSONField(default=list)
    os_name = models.CharField(max_length=255, blank=True)
    os_version = models.CharField(max_length=128, blank=True)
    windows_build = models.CharField(max_length=128, blank=True)
    cpu = models.CharField(max_length=255, blank=True)
    memory_total_bytes = models.BigIntegerField(null=True, blank=True)
    disks = models.JSONField(default=list)
    manufacturer = models.CharField(max_length=255, blank=True)
    model = models.CharField(max_length=255, blank=True)
    serial_number = models.CharField(max_length=255, blank=True)
    uptime_seconds = models.BigIntegerField(null=True, blank=True)
    installed_software = models.JSONField(default=list)
    defender_status = models.JSONField(default=dict)
    raw_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['machine', '-received_at']),
            models.Index(fields=['hostname']),
        ]

    def __str__(self) -> str:
        return f'{self.hostname} @ {self.received_at:%Y-%m-%d %H:%M:%S}'


class SoftwarePolicy(models.Model):
    TYPE_PERMITTED = 'permitted'
    TYPE_PROHIBITED = 'prohibited'
    TYPE_REQUIRED = 'required'
    TYPE_RESTRICTED = 'restricted'
    TYPE_OBSERVED = 'observed'
    TYPE_CHOICES = [
        (TYPE_PERMITTED, 'Permitido'),
        (TYPE_PROHIBITED, 'Proibido'),
        (TYPE_REQUIRED, 'Obrigatorio'),
        (TYPE_RESTRICTED, 'Restrito'),
        (TYPE_OBSERVED, 'Observado'),
    ]

    MATCH_CONTAINS = 'contains'
    MATCH_EQUALS = 'equals'
    MATCH_STARTS_WITH = 'starts_with'
    MATCH_CHOICES = [
        (MATCH_CONTAINS, 'Contem'),
        (MATCH_EQUALS, 'Igual'),
        (MATCH_STARTS_WITH, 'Comeca com'),
    ]

    SCOPE_ALL = 'all'
    SCOPE_HOSTNAME_PREFIX = 'hostname_prefix'
    SCOPE_HOSTNAME_CONTAINS = 'hostname_contains'
    SCOPE_DOMAIN = 'domain'
    SCOPE_SPECIFIC_ENDPOINTS = 'specific_endpoints'
    SCOPE_CHOICES = [
        (SCOPE_ALL, 'Todos os endpoints'),
        (SCOPE_HOSTNAME_PREFIX, 'Hostname comeca com'),
        (SCOPE_HOSTNAME_CONTAINS, 'Hostname contem'),
        (SCOPE_DOMAIN, 'Dominio'),
        (SCOPE_SPECIFIC_ENDPOINTS, 'Endpoints especificos'),
    ]

    SEVERITY_CRITICAL = 'critical'
    SEVERITY_SECURITY = 'security'
    SEVERITY_WARNING = 'warning'
    SEVERITY_INFO = 'info'
    SEVERITY_CHOICES = [
        (SEVERITY_CRITICAL, 'Critical'),
        (SEVERITY_SECURITY, 'Security'),
        (SEVERITY_WARNING, 'Warning'),
        (SEVERITY_INFO, 'Info'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    policy_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    software_name = models.CharField(max_length=180)
    match_type = models.CharField(max_length=30, choices=MATCH_CHOICES, default=MATCH_CONTAINS)
    publisher = models.CharField(max_length=180, blank=True)
    version_rule = models.CharField(max_length=180, blank=True)
    scope_type = models.CharField(max_length=40, choices=SCOPE_CHOICES, default=SCOPE_ALL)
    scope_value = models.CharField(max_length=255, blank=True)
    severity = models.CharField(max_length=30, choices=SEVERITY_CHOICES, default=SEVERITY_INFO)
    is_active = models.BooleanField(default=True)
    monitor_only = models.BooleanField(default=False)
    create_alert = models.BooleanField(default=True)
    show_in_noc = models.BooleanField(default=True)
    create_audit_event = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['policy_type', 'is_active']),
            models.Index(fields=['severity', 'is_active']),
            models.Index(fields=['scope_type']),
            models.Index(fields=['software_name']),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.scope_type not in {self.SCOPE_ALL, self.SCOPE_SPECIFIC_ENDPOINTS} and not self.scope_value:
            raise ValidationError({'scope_value': 'Informe o valor do escopo para este tipo de politica.'})
        if self.policy_type == self.TYPE_OBSERVED and self.monitor_only is None:
            self.monitor_only = True


class SoftwarePolicyException(models.Model):
    TYPE_TEMPORARY = 'temporary'
    TYPE_PERMANENT = 'permanent'
    TYPE_CHOICES = [
        (TYPE_TEMPORARY, 'Temporaria'),
        (TYPE_PERMANENT, 'Permanente'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy = models.ForeignKey(
        SoftwarePolicy,
        on_delete=models.CASCADE,
        related_name='exceptions',
    )
    endpoint = models.ForeignKey(
        AgentMachine,
        on_delete=models.CASCADE,
        related_name='software_policy_exceptions',
    )
    reason = models.TextField(blank=True)
    exception_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_TEMPORARY)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['policy', 'is_active']),
            models.Index(fields=['endpoint', 'is_active']),
            models.Index(fields=['exception_type', 'expires_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['policy', 'endpoint'],
                condition=models.Q(is_active=True),
                name='unique_active_software_policy_exception',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.policy} - {self.endpoint}'

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.exception_type == self.TYPE_TEMPORARY and self.expires_at is None:
            raise ValidationError({'expires_at': 'Excecao temporaria exige data de expiracao.'})

    @property
    def status_key(self) -> str:
        if not self.is_active:
            return 'inactive'
        if self.exception_type == self.TYPE_PERMANENT:
            return 'permanent'
        if self.expires_at and self.expires_at <= timezone.now():
            return 'expired'
        return 'active'

    @property
    def status_label(self) -> str:
        return {
            'inactive': 'Inativa',
            'permanent': 'Permanente',
            'expired': 'Expirada',
            'active': 'Ativa',
        }[self.status_key]


class SoftwarePolicyTargetEndpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy = models.ForeignKey(
        SoftwarePolicy,
        on_delete=models.CASCADE,
        related_name='target_endpoints',
    )
    endpoint = models.ForeignKey(
        AgentMachine,
        on_delete=models.CASCADE,
        related_name='targeted_software_policies',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['endpoint__hostname']
        indexes = [
            models.Index(fields=['policy']),
            models.Index(fields=['endpoint']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['policy', 'endpoint'],
                name='unique_software_policy_target_endpoint',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.policy} -> {self.endpoint}'


class SoftwarePolicyViolation(models.Model):
    STATUS_OPEN = 'open'
    STATUS_RESOLVED = 'resolved'
    STATUS_IGNORED = 'ignored'
    STATUS_EXCEPTION_APPLIED = 'exception_applied'
    STATUS_CHOICES = [
        (STATUS_OPEN, 'Open'),
        (STATUS_RESOLVED, 'Resolved'),
        (STATUS_IGNORED, 'Ignored'),
        (STATUS_EXCEPTION_APPLIED, 'Exception applied'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy = models.ForeignKey(
        SoftwarePolicy,
        on_delete=models.CASCADE,
        related_name='violations',
    )
    endpoint = models.ForeignKey(
        AgentMachine,
        on_delete=models.CASCADE,
        related_name='software_policy_violations',
    )
    snapshot = models.ForeignKey(
        InventorySnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='software_policy_violations',
    )
    software_name = models.CharField(max_length=180, blank=True)
    software_version = models.CharField(max_length=120, blank=True)
    publisher = models.CharField(max_length=180, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_OPEN)
    severity = models.CharField(max_length=30, choices=SoftwarePolicy.SEVERITY_CHOICES)
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_reason = models.CharField(max_length=120, blank=True)
    alert = models.ForeignKey(
        'EndpointAlert',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='software_policy_violations',
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-last_seen_at']
        indexes = [
            models.Index(fields=['policy', 'endpoint', 'status']),
            models.Index(fields=['endpoint', 'status', '-last_seen_at']),
            models.Index(fields=['severity', 'status']),
            models.Index(fields=['software_name']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['policy', 'endpoint', 'software_name'],
                condition=models.Q(status='open'),
                name='unique_open_software_policy_violation',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.policy} - {self.endpoint} - {self.software_name or "software"}'


class EndpointAlert(models.Model):
    SEVERITY_INFO = 'info'
    SEVERITY_WARNING = 'warning'
    SEVERITY_CRITICAL = 'critical'
    SEVERITY_SECURITY = 'security'
    SEVERITY_CHOICES = [
        (SEVERITY_INFO, 'Info'),
        (SEVERITY_WARNING, 'Warning'),
        (SEVERITY_CRITICAL, 'Critical'),
        (SEVERITY_SECURITY, 'Security'),
    ]

    STATUS_OPEN = 'open'
    STATUS_ACKNOWLEDGED = 'acknowledged'
    STATUS_RESOLVED = 'resolved'
    STATUS_CHOICES = [
        (STATUS_OPEN, 'Open'),
        (STATUS_ACKNOWLEDGED, 'Acknowledged'),
        (STATUS_RESOLVED, 'Resolved'),
    ]
    RESOLUTION_AUTOMATIC = 'automatic'
    RESOLUTION_MANUAL = 'manual'
    RESOLUTION_CHOICES = [
        (RESOLUTION_AUTOMATIC, 'Automatic'),
        (RESOLUTION_MANUAL, 'Manual'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    endpoint = models.ForeignKey(
        AgentMachine,
        on_delete=models.CASCADE,
        related_name='alerts',
    )
    alert_type = models.CharField(max_length=128)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES)
    title = models.CharField(max_length=255)
    description = models.TextField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_type = models.CharField(max_length=16, choices=RESOLUTION_CHOICES, blank=True)
    muted_until = models.DateTimeField(null=True, blank=True)
    muted_at = models.DateTimeField(null=True, blank=True)
    muted_reason = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_temporary = models.BooleanField(default=False)
    source = models.CharField(max_length=50, blank=True, default='')
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-last_seen_at']
        indexes = [
            models.Index(fields=['endpoint', 'alert_type', 'status']),
            models.Index(fields=['severity', 'status']),
            models.Index(fields=['last_seen_at']),
            models.Index(fields=['is_temporary', 'expires_at']),
        ]

    def __str__(self) -> str:
        return f'{self.get_severity_display()} - {self.title}'

    @property
    def is_muted(self) -> bool:
        return bool(self.muted_until and self.muted_until > timezone.now())


class AlertEvent(models.Model):
    TYPE_CREATED = 'created'
    TYPE_UPDATED = 'updated'
    TYPE_ACKNOWLEDGED = 'acknowledged'
    TYPE_RESOLVED_MANUAL = 'resolved_manual'
    TYPE_RESOLVED_AUTOMATIC = 'resolved_automatic'
    TYPE_MUTED = 'muted'
    TYPE_COMMENT = 'comment'
    TYPE_REOPENED = 'reopened'
    TYPE_CHOICES = [
        (TYPE_CREATED, 'Created'),
        (TYPE_UPDATED, 'Updated'),
        (TYPE_ACKNOWLEDGED, 'Acknowledged'),
        (TYPE_RESOLVED_MANUAL, 'Resolved manually'),
        (TYPE_RESOLVED_AUTOMATIC, 'Resolved automatically'),
        (TYPE_MUTED, 'Muted'),
        (TYPE_COMMENT, 'Comment'),
        (TYPE_REOPENED, 'Reopened'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    alert = models.ForeignKey(
        EndpointAlert,
        on_delete=models.CASCADE,
        related_name='events',
    )
    event_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    message = models.TextField()
    metadata = models.JSONField(default=dict)
    actor = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['alert', '-created_at']),
            models.Index(fields=['event_type', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.get_event_type_display()} - {self.alert_id}'


class AuditEvent(models.Model):
    SEVERITY_DEBUG = 'debug'
    SEVERITY_INFO = 'info'
    SEVERITY_SUCCESS = 'success'
    SEVERITY_WARNING = 'warning'
    SEVERITY_CRITICAL = 'critical'
    SEVERITY_SECURITY = 'security'
    SEVERITY_CHOICES = [
        (SEVERITY_DEBUG, 'Debug'),
        (SEVERITY_INFO, 'Info'),
        (SEVERITY_SUCCESS, 'Success'),
        (SEVERITY_WARNING, 'Warning'),
        (SEVERITY_CRITICAL, 'Critical'),
        (SEVERITY_SECURITY, 'Security'),
    ]

    ACTOR_SYSTEM = 'system'
    ACTOR_USER = 'user'
    ACTOR_AGENT = 'agent'
    ACTOR_SCHEDULER = 'scheduler'
    ACTOR_CHOICES = [
        (ACTOR_SYSTEM, 'System'),
        (ACTOR_USER, 'User'),
        (ACTOR_AGENT, 'Agent'),
        (ACTOR_SCHEDULER, 'Scheduler'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=80)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default=SEVERITY_INFO)
    actor_type = models.CharField(max_length=30, choices=ACTOR_CHOICES, default=ACTOR_SYSTEM)
    actor_name = models.CharField(max_length=150, blank=True)
    endpoint = models.ForeignKey(
        AgentMachine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_events',
    )
    alert = models.ForeignKey(
        EndpointAlert,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_events',
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['event_type']),
            models.Index(fields=['severity']),
            models.Index(fields=['actor_type']),
            models.Index(fields=['endpoint', '-created_at']),
            models.Index(fields=['alert', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.event_type} - {self.title}'


class AgentJob(models.Model):
    TYPE_FORCE_INVENTORY = 'force_inventory'
    TYPE_COLLECT_DISKS = 'collect_disks'
    TYPE_COLLECT_SECURITY = 'collect_security'
    TYPE_COLLECT_SOFTWARE = 'collect_software'
    TYPE_PING = 'ping'
    TYPE_COLLECT_LOGS = 'collect_logs'
    TYPE_WINDOWS_UPDATE_SCAN = 'windows_update_scan'
    TYPE_CHOICES = [
        (TYPE_FORCE_INVENTORY, 'Force inventory'),
        (TYPE_COLLECT_DISKS, 'Collect disks'),
        (TYPE_COLLECT_SECURITY, 'Collect security'),
        (TYPE_COLLECT_SOFTWARE, 'Collect software'),
        (TYPE_PING, 'Ping'),
        (TYPE_COLLECT_LOGS, 'Collect logs'),
        (TYPE_WINDOWS_UPDATE_SCAN, 'Windows Update scan'),
    ]

    STATUS_QUEUED = 'queued'
    STATUS_SENT = 'sent'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_EXPIRED = 'expired'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_QUEUED, 'Queued'),
        (STATUS_SENT, 'Sent'),
        (STATUS_RUNNING, 'Running'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_EXPIRED, 'Expired'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    endpoint = models.ForeignKey(
        AgentMachine,
        on_delete=models.CASCADE,
        related_name='jobs',
    )
    job_type = models.CharField(max_length=80, choices=TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    created_by = models.CharField(max_length=150, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    stdout = models.TextField(blank=True)
    stderr = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    queued_at = models.DateTimeField(default=timezone.now)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['endpoint', 'status', '-created_at']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['job_type', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.job_type} - {self.endpoint} - {self.status}'

    @property
    def is_pending_for_agent(self) -> bool:
        if self.status != self.STATUS_QUEUED:
            return False
        return not self.expires_at or self.expires_at > timezone.now()


class MaintenanceRun(models.Model):
    STATUS_RUNNING = 'running'
    STATUS_SUCCESS = 'success'
    STATUS_PARTIAL = 'partial'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_RUNNING, 'Running'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_PARTIAL, 'Partial'),
        (STATUS_FAILED, 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    triggered_by = models.CharField(max_length=80, default='manual')
    dry_run = models.BooleanField(default=False)
    total_tasks = models.PositiveIntegerField(default=0)
    successful_tasks = models.PositiveIntegerField(default=0)
    failed_tasks = models.PositiveIntegerField(default=0)
    skipped_tasks = models.PositiveIntegerField(default=0)
    duration_seconds = models.FloatField(null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['status', '-started_at']),
            models.Index(fields=['-started_at']),
            models.Index(fields=['dry_run', '-started_at']),
        ]

    def __str__(self) -> str:
        return f'Maintenance {self.started_at:%Y-%m-%d %H:%M:%S} - {self.status}'


class MaintenanceTaskResult(models.Model):
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'
    STATUS_SKIPPED = 'skipped'
    STATUS_CHOICES = [
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_SKIPPED, 'Skipped'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        MaintenanceRun,
        on_delete=models.CASCADE,
        related_name='task_results',
    )
    task_name = models.CharField(max_length=120)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    output = models.TextField(blank=True)
    error = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['started_at']
        indexes = [
            models.Index(fields=['run', 'started_at']),
            models.Index(fields=['task_name']),
            models.Index(fields=['status']),
        ]

    def __str__(self) -> str:
        return f'{self.task_name} - {self.status}'
