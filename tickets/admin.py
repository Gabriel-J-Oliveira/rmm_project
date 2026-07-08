from django.contrib import admin

from .models import (
    DeskQueue,
    DeskSLA,
    DeskTemplate,
    InboundEmailMessage,
    NotificationOutbox,
    Ticket,
    TicketAttachment,
    TicketAuditEvent,
    TicketCategory,
    TicketComment,
)


@admin.register(TicketCategory)
class TicketCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'default_priority', 'default_queue', 'default_sla', 'color', 'icon', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_active', 'default_priority', 'created_at')
    search_fields = ('name', 'description', 'icon', 'color')


@admin.register(DeskQueue)
class DeskQueueAdmin(admin.ModelAdmin):
    list_display = ('name', 'responsible', 'capacity', 'receives_tickets', 'receives_rmm', 'receives_gmud', 'is_active')
    list_filter = ('is_active', 'receives_tickets', 'receives_rmm', 'receives_gmud')
    search_fields = ('name', 'description', 'responsible')


@admin.register(DeskSLA)
class DeskSLAAdmin(admin.ModelAdmin):
    list_display = ('name', 'priority', 'first_response_minutes', 'resolution_minutes', 'calendar_type', 'is_active')
    list_filter = ('is_active', 'priority', 'calendar_type')
    search_fields = ('name', 'description')


@admin.register(DeskTemplate)
class DeskTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'template_type', 'application', 'category', 'channel', 'subject', 'is_active', 'updated_at')
    list_filter = ('is_active', 'template_type', 'application', 'channel')
    search_fields = ('name', 'description', 'subject', 'content', 'application')


@admin.register(NotificationOutbox)
class NotificationOutboxAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'source_app', 'event_type', 'ticket', 'recipient_email', 'priority', 'attempts', 'status', 'sent_at')
    list_filter = ('source_app', 'status', 'priority', 'channel', 'event_type', 'created_at')
    search_fields = ('source_id', 'ticket__number', 'ticket__title', 'recipient_name', 'recipient_email', 'subject', 'body_text')
    readonly_fields = (
        'id', 'source_app', 'source_model', 'source_id', 'ticket', 'template',
        'event_type', 'channel', 'recipient_name', 'recipient_email', 'cc', 'bcc',
        'subject', 'body_text', 'body_html', 'status', 'priority', 'attempts',
        'max_attempts', 'last_error', 'last_attempt_at', 'created_at', 'updated_at',
        'sent_at', 'metadata',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InboundEmailMessage)
class InboundEmailMessageAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'status', 'ticket', 'from_email', 'subject', 'received_at', 'processed_at')
    list_filter = ('status', 'created_at', 'processed_at')
    search_fields = ('message_id', 'ticket__number', 'ticket__title', 'from_name', 'from_email', 'subject', 'error')
    readonly_fields = (
        'id', 'message_id', 'ticket', 'from_name', 'from_email', 'subject',
        'received_at', 'processed_at', 'status', 'error', 'raw_metadata',
        'created_comment', 'created_at',
    )

    def has_add_permission(self, request):
        return False


class TicketCommentInline(admin.TabularInline):
    model = TicketComment
    extra = 0
    readonly_fields = ('created_at',)


class TicketAttachmentInline(admin.TabularInline):
    model = TicketAttachment
    extra = 0
    readonly_fields = ('created_at',)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        'number',
        'title',
        'status',
        'priority',
        'category',
        'requester_name',
        'queue',
        'sla',
        'due_at',
        'assigned_to',
        'endpoint',
        'created_at',
        'updated_at',
    )
    list_filter = ('status', 'priority', 'category', 'sla', 'requester_is_partner', 'created_at')
    search_fields = (
        '=number',
        'title',
        'requester_name',
        'requester_email',
        'requester_username',
        'assigned_to',
        'endpoint__hostname',
    )
    readonly_fields = ('number', 'created_at', 'updated_at', 'first_response_at', 'assigned_at', 'resolved_at', 'closed_at')
    inlines = (TicketCommentInline, TicketAttachmentInline)


@admin.register(TicketComment)
class TicketCommentAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'author_name', 'visibility', 'created_at')
    list_filter = ('visibility', 'created_at')
    search_fields = ('ticket__title', 'ticket__number', 'author_name', 'body')
    readonly_fields = ('created_at',)


@admin.register(TicketAttachment)
class TicketAttachmentAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'original_name', 'visibility', 'uploaded_by', 'size', 'created_at')
    list_filter = ('visibility', 'created_at')
    search_fields = ('ticket__number', 'ticket__title', 'original_name', 'uploaded_by')
    readonly_fields = ('created_at',)


@admin.register(TicketAuditEvent)
class TicketAuditEventAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'actor', 'event_type', 'action', 'field_name', 'created_at')
    list_filter = ('event_type', 'created_at')
    search_fields = ('ticket__number', 'ticket__title', 'actor', 'action', 'field_name', 'old_value', 'new_value')
    readonly_fields = ('created_at',)
