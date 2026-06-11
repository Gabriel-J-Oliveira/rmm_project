from django.contrib import admin

from .models import Ticket, TicketCategory, TicketComment


@admin.register(TicketCategory)
class TicketCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'color', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'description')


class TicketCommentInline(admin.TabularInline):
    model = TicketComment
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
        'assigned_to',
        'endpoint',
        'created_at',
        'updated_at',
    )
    list_filter = ('status', 'priority', 'category', 'requester_is_partner', 'created_at')
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
    inlines = (TicketCommentInline,)


@admin.register(TicketComment)
class TicketCommentAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'author_name', 'visibility', 'created_at')
    list_filter = ('visibility', 'created_at')
    search_fields = ('ticket__title', 'ticket__number', 'author_name', 'body')
    readonly_fields = ('created_at',)
