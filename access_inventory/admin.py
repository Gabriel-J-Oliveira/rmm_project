from django.contrib import admin

from .models import (
    ADGroup,
    ADGroupMembership,
    ADOrganizationalUnit,
    ADUser,
    AccessReviewFolder,
    AccessReviewPlan,
    AccessReviewPrincipal,
    AccessReviewRule,
    AclEntry,
    FileServer,
    Folder,
    InventoryAgent,
    InventoryAgentRun,
    Share,
)


@admin.register(ADOrganizationalUnit)
class ADOrganizationalUnitAdmin(admin.ModelAdmin):
    list_display = ('name', 'distinguished_name', 'parent_distinguished_name', 'updated_at')
    search_fields = ('name', 'distinguished_name', 'parent_distinguished_name')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(InventoryAgent)
class InventoryAgentAdmin(admin.ModelAdmin):
    list_display = ('name', 'hostname', 'enabled', 'version', 'last_seen_at', 'updated_at')
    list_filter = ('enabled', 'created_at', 'last_seen_at')
    search_fields = ('name', 'hostname', 'description', 'version')
    readonly_fields = ('token_hash', 'last_seen_at', 'created_at', 'updated_at')


@admin.register(InventoryAgentRun)
class InventoryAgentRunAdmin(admin.ModelAdmin):
    list_display = (
        'started_at',
        'agent',
        'run_type',
        'status',
        'items_created',
        'items_updated',
        'items_ignored',
        'errors_count',
        'finished_at',
    )
    list_filter = ('run_type', 'status', 'started_at', 'agent')
    search_fields = ('agent__name', 'agent__hostname', 'message')
    readonly_fields = (
        'agent',
        'run_type',
        'status',
        'started_at',
        'finished_at',
        'message',
        'items_created',
        'items_updated',
        'items_ignored',
        'errors_count',
        'created_at',
        'updated_at',
    )


class AccessReviewFolderInline(admin.TabularInline):
    model = AccessReviewFolder
    extra = 0
    fields = ('area_name', 'name', 'proposed_path', 'parent', 'current_folder', 'sort_order')
    autocomplete_fields = ('parent', 'current_folder')


class AccessReviewPrincipalInline(admin.TabularInline):
    model = AccessReviewPrincipal
    extra = 0
    fields = ('principal_type', 'display_name', 'sam_account_name', 'proposed_group_name', 'ad_user', 'ad_group')
    autocomplete_fields = ('ad_user', 'ad_group')


class AccessReviewRuleInline(admin.TabularInline):
    model = AccessReviewRule
    extra = 0
    fields = ('folder', 'principal', 'permission_level', 'permission_label', 'source')
    autocomplete_fields = ('folder', 'principal')


@admin.register(AccessReviewPlan)
class AccessReviewPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'status', 'current_snapshot_label', 'created_by', 'updated_at')
    list_filter = ('status', 'created_at', 'updated_at')
    search_fields = ('name', 'description', 'current_snapshot_label', 'notes')
    readonly_fields = ('created_at', 'updated_at')
    inlines = (AccessReviewFolderInline, AccessReviewPrincipalInline, AccessReviewRuleInline)


@admin.register(AccessReviewFolder)
class AccessReviewFolderAdmin(admin.ModelAdmin):
    list_display = ('plan', 'area_name', 'name', 'proposed_path', 'parent', 'current_folder', 'sort_order', 'updated_at')
    list_filter = ('plan', 'area_name')
    search_fields = ('name', 'area_name', 'proposed_path', 'notes', 'current_folder__path')
    autocomplete_fields = ('plan', 'parent', 'current_folder')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(AccessReviewPrincipal)
class AccessReviewPrincipalAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'principal_type', 'plan', 'sam_account_name', 'proposed_group_name', 'ad_user', 'ad_group')
    list_filter = ('plan', 'principal_type')
    search_fields = (
        'display_name',
        'sam_account_name',
        'proposed_group_name',
        'notes',
        'ad_user__sam_account_name',
        'ad_group__sam_account_name',
    )
    autocomplete_fields = ('plan', 'ad_user', 'ad_group')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(AccessReviewRule)
class AccessReviewRuleAdmin(admin.ModelAdmin):
    list_display = ('folder', 'principal', 'permission_level', 'permission_label', 'source', 'plan', 'updated_at')
    list_filter = ('plan', 'permission_level', 'source', 'folder__area_name')
    search_fields = (
        'folder__name',
        'folder__proposed_path',
        'principal__display_name',
        'principal__sam_account_name',
        'permission_label',
        'permission_explanation',
        'notes',
    )
    autocomplete_fields = ('plan', 'folder', 'principal')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ADUser)
class ADUserAdmin(admin.ModelAdmin):
    list_display = ('sam_account_name', 'display_name', 'email', 'enabled', 'ou', 'updated_at')
    list_filter = ('enabled', 'ou')
    search_fields = ('sid', 'sam_account_name', 'display_name', 'email', 'distinguished_name')
    autocomplete_fields = ('ou',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ADGroup)
class ADGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'sam_account_name', 'ou', 'updated_at')
    list_filter = ('ou',)
    search_fields = ('sid', 'sam_account_name', 'name', 'description', 'distinguished_name')
    autocomplete_fields = ('ou',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ADGroupMembership)
class ADGroupMembershipAdmin(admin.ModelAdmin):
    list_display = ('parent_group', 'member_user', 'member_group', 'updated_at')
    list_filter = ('parent_group',)
    search_fields = (
        'parent_group__sid',
        'parent_group__name',
        'member_user__sid',
        'member_user__sam_account_name',
        'member_group__sid',
        'member_group__name',
    )
    autocomplete_fields = ('parent_group', 'member_user', 'member_group')
    readonly_fields = ('created_at', 'updated_at')


class ShareInline(admin.TabularInline):
    model = Share
    extra = 0
    readonly_fields = ('created_at', 'updated_at')


@admin.register(FileServer)
class FileServerAdmin(admin.ModelAdmin):
    list_display = ('name', 'fqdn', 'rmm_agent', 'updated_at')
    search_fields = ('name', 'fqdn', 'description', 'rmm_agent__hostname', 'rmm_agent__fqdn')
    autocomplete_fields = ('rmm_agent',)
    readonly_fields = ('created_at', 'updated_at')
    inlines = (ShareInline,)


@admin.register(Share)
class ShareAdmin(admin.ModelAdmin):
    list_display = ('name', 'file_server', 'unc_path', 'updated_at')
    list_filter = ('file_server',)
    search_fields = ('name', 'unc_path', 'description', 'file_server__name', 'file_server__fqdn')
    autocomplete_fields = ('file_server',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ('path', 'share', 'file_server', 'inheritance_enabled', 'updated_at')
    list_filter = ('inheritance_enabled', 'share__file_server')
    search_fields = ('path', 'parent_path', 'share__unc_path', 'share__file_server__name')
    autocomplete_fields = ('share',)
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(ordering='share__file_server__name')
    def file_server(self, obj):
        return obj.share.file_server


@admin.register(AclEntry)
class AclEntryAdmin(admin.ModelAdmin):
    list_display = (
        'identity_name',
        'identity_sid',
        'resolved_identity_type',
        'resolved_ad_user',
        'resolved_ad_group',
        'rights',
        'is_inherited',
        'folder',
        'updated_at',
    )
    list_filter = ('resolved_identity_type', 'inherited', 'access_type', 'folder__share', 'folder__share__file_server')
    search_fields = (
        'identity_sid',
        'identity_name',
        'rights',
        'folder__path',
        'folder__share__unc_path',
        'ad_user__sam_account_name',
        'ad_group__name',
        'resolved_ad_user__sam_account_name',
        'resolved_ad_user__display_name',
        'resolved_ad_group__sam_account_name',
        'resolved_ad_group__name',
    )
    autocomplete_fields = ('folder', 'ad_user', 'ad_group', 'resolved_ad_user', 'resolved_ad_group')
    readonly_fields = ('created_at', 'updated_at', 'resolved_at')

    @admin.display(boolean=True, ordering='inherited', description='Inherited')
    def is_inherited(self, obj):
        return obj.inherited
