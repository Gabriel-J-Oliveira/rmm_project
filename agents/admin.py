from django.contrib import admin

from .models import (
    AgentEnrollmentLog,
    AgentEnrollmentToken,
    AgentMachine,
    AgentManualValidationToken,
    AlertEvent,
    AuditEvent,
    EndpointAlert,
    InventorySnapshot,
    MaintenanceRun,
    MaintenanceTaskResult,
    SoftwarePolicy,
    SoftwarePolicyException,
    SoftwarePolicyTargetEndpoint,
    SoftwarePolicyViolation,
)


@admin.register(AgentMachine)
class AgentMachineAdmin(admin.ModelAdmin):
    list_display = (
        'hostname',
        'domain',
        'status',
        'agent_version',
        'last_seen_at',
        'last_ip',
        'last_logged_user',
        'os_name',
        'is_active',
    )
    list_filter = ('domain', 'status', 'is_active', 'os_name', 'agent_version')
    search_fields = ('hostname', 'serial_number', 'last_logged_user', 'agent_version')
    readonly_fields = (
        'id',
        'agent_token_hash',
        'first_seen_at',
        'last_seen_at',
        'created_at',
        'updated_at',
    )
    fieldsets = (
        (None, {
            'fields': (
                'id',
                'hostname',
                'domain',
                'fqdn',
                'status',
                'is_active',
                'agent_token_hash',
            ),
        }),
        ('Agente Night Owl', {
            'fields': (
                'agent_version',
                'agent_mode',
                'agent_install_path',
                'agent_task_name',
                'agent_runtime',
                'agent_runtime_version',
                'agent_update_source',
                'agent_reported_at',
            ),
        }),
        ('Ultima comunicacao', {
            'fields': (
                'first_seen_at',
                'last_seen_at',
                'last_ip',
                'last_logged_user',
            ),
        }),
        ('Inventario resumido', {
            'fields': (
                'os_name',
                'os_version',
                'windows_build',
                'manufacturer',
                'model',
                'serial_number',
            ),
        }),
        ('Controle', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(InventorySnapshot)
class InventorySnapshotAdmin(admin.ModelAdmin):
    list_display = (
        'machine',
        'collected_at',
        'received_at',
        'os_name',
        'logged_user',
    )
    list_filter = ('received_at', 'os_name')
    search_fields = ('hostname', 'logged_user', 'serial_number')
    readonly_fields = tuple(field.name for field in InventorySnapshot._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class SoftwarePolicyTargetEndpointInline(admin.TabularInline):
    model = SoftwarePolicyTargetEndpoint
    extra = 0
    autocomplete_fields = ('endpoint',)
    readonly_fields = ('created_at',)


@admin.register(SoftwarePolicy)
class SoftwarePolicyAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'policy_type',
        'software_name',
        'match_type',
        'scope_type',
        'severity',
        'is_active',
        'monitor_only',
        'updated_at',
    )
    list_filter = ('policy_type', 'severity', 'is_active', 'monitor_only', 'scope_type')
    search_fields = ('name', 'software_name', 'publisher', 'description', 'scope_value')
    readonly_fields = ('id', 'created_at', 'updated_at')
    inlines = (SoftwarePolicyTargetEndpointInline,)


@admin.register(SoftwarePolicyException)
class SoftwarePolicyExceptionAdmin(admin.ModelAdmin):
    list_display = (
        'policy',
        'endpoint',
        'exception_type',
        'expires_at',
        'is_active',
        'created_at',
    )
    list_filter = ('exception_type', 'is_active', 'expires_at')
    search_fields = ('policy__name', 'endpoint__hostname', 'reason')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(SoftwarePolicyTargetEndpoint)
class SoftwarePolicyTargetEndpointAdmin(admin.ModelAdmin):
    list_display = ('policy', 'endpoint', 'created_at')
    search_fields = ('policy__name', 'endpoint__hostname')
    readonly_fields = ('id', 'created_at')


@admin.register(SoftwarePolicyViolation)
class SoftwarePolicyViolationAdmin(admin.ModelAdmin):
    list_display = (
        'policy',
        'endpoint',
        'software_name',
        'severity',
        'status',
        'first_seen_at',
        'last_seen_at',
        'resolved_at',
    )
    list_filter = ('status', 'severity', 'policy__policy_type', 'created_at', 'resolved_at')
    search_fields = ('policy__name', 'endpoint__hostname', 'software_name', 'publisher', 'resolution_reason')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(AgentEnrollmentToken)
class AgentEnrollmentTokenAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'prefix',
        'is_active',
        'expires_at',
        'used_count',
        'max_uses',
        'allowed_domain',
        'last_used_at',
        'created_at',
    )
    list_filter = ('is_active', 'allowed_domain', 'created_at', 'expires_at')
    search_fields = ('name', 'prefix', 'notes', 'allowed_domain')
    readonly_fields = ('id', 'token_hash', 'prefix', 'used_count', 'last_used_at', 'created_at', 'updated_at')


@admin.register(AgentEnrollmentLog)
class AgentEnrollmentLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at',
        'status',
        'hostname',
        'domain',
        'endpoint',
        'enrollment_token',
        'ip_address',
    )
    list_filter = ('status', 'domain', 'created_at')
    search_fields = ('hostname', 'domain', 'serial_number', 'message')
    readonly_fields = tuple(field.name for field in AgentEnrollmentLog._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AgentManualValidationToken)
class AgentManualValidationTokenAdmin(admin.ModelAdmin):
    list_display = (
        'created_at',
        'prefix',
        'name',
        'is_active',
        'expires_at',
        'used_at',
        'used_by_hostname',
        'used_by_domain',
    )
    list_filter = ('is_active', 'created_at', 'expires_at', 'used_at')
    search_fields = ('prefix', 'name', 'used_by_hostname', 'used_by_domain', 'notes')
    readonly_fields = (
        'id',
        'token_hash',
        'prefix',
        'used_at',
        'used_by_hostname',
        'used_by_domain',
        'created_at',
    )


@admin.register(EndpointAlert)
class EndpointAlertAdmin(admin.ModelAdmin):
    list_display = (
        'severity',
        'status',
        'alert_type',
        'title',
        'endpoint',
        'first_seen_at',
        'last_seen_at',
        'resolved_at',
        'muted_until',
        'is_temporary',
        'expires_at',
        'source',
    )
    list_filter = ('severity', 'status', 'alert_type', 'resolution_type', 'is_temporary', 'source', 'created_at')
    search_fields = (
        'title',
        'description',
        'endpoint__hostname',
        'endpoint__last_logged_user',
    )
    readonly_fields = (
        'id',
        'first_seen_at',
        'last_seen_at',
        'acknowledged_at',
        'resolved_at',
        'muted_at',
        'created_at',
        'updated_at',
    )


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'alert', 'actor', 'created_at')
    list_filter = ('event_type', 'created_at')
    search_fields = ('message', 'alert__title', 'alert__endpoint__hostname', 'actor')
    readonly_fields = tuple(field.name for field in AlertEvent._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = (
        'created_at',
        'severity',
        'event_type',
        'title',
        'endpoint',
        'alert',
        'actor_type',
        'actor_name',
    )
    list_filter = ('severity', 'event_type', 'actor_type', 'created_at')
    search_fields = ('title', 'description', 'endpoint__hostname', 'actor_name', 'metadata')
    readonly_fields = tuple(field.name for field in AuditEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class MaintenanceTaskResultInline(admin.TabularInline):
    model = MaintenanceTaskResult
    extra = 0
    can_delete = False
    readonly_fields = tuple(field.name for field in MaintenanceTaskResult._meta.fields)
    fields = (
        'task_name',
        'status',
        'started_at',
        'finished_at',
        'duration_seconds',
        'output',
        'error',
        'metadata',
    )

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(MaintenanceRun)
class MaintenanceRunAdmin(admin.ModelAdmin):
    list_display = (
        'started_at',
        'finished_at',
        'status',
        'triggered_by',
        'dry_run',
        'total_tasks',
        'successful_tasks',
        'failed_tasks',
        'skipped_tasks',
        'duration_seconds',
    )
    list_filter = ('status', 'dry_run', 'triggered_by', 'started_at')
    search_fields = ('id', 'error', 'summary')
    readonly_fields = tuple(field.name for field in MaintenanceRun._meta.fields)
    inlines = (MaintenanceTaskResultInline,)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(MaintenanceTaskResult)
class MaintenanceTaskResultAdmin(admin.ModelAdmin):
    list_display = (
        'task_name',
        'status',
        'run',
        'started_at',
        'finished_at',
        'duration_seconds',
    )
    list_filter = ('status', 'task_name', 'started_at')
    search_fields = ('task_name', 'output', 'error', 'run__id')
    readonly_fields = tuple(field.name for field in MaintenanceTaskResult._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
