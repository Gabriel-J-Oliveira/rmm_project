from django.contrib import admin

from .models import OperationalTask, TaskReminderLog


@admin.register(OperationalTask)
class OperationalTaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'priority', 'category', 'responsible', 'due_at', 'updated_at')
    list_filter = ('status', 'priority', 'category')
    search_fields = ('title', 'description', 'responsible', 'linked_user', 'linked_ticket_ref', 'linked_endpoint_name')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(TaskReminderLog)
class TaskReminderLogAdmin(admin.ModelAdmin):
    list_display = ('task', 'reminder_type', 'sent_to', 'status', 'sent_at', 'created_at')
    list_filter = ('reminder_type', 'status', 'created_at')
    search_fields = ('task__title', 'sent_to', 'error_message')
    readonly_fields = ('created_at',)
