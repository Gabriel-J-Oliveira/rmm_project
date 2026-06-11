from django.contrib import admin

from .models import (
    ADGroup,
    ADGroupMembership,
    ADOrganizationalUnit,
    ADUser,
    AclEntry,
    FileServer,
    Folder,
    Share,
)


@admin.register(ADOrganizationalUnit)
class ADOrganizationalUnitAdmin(admin.ModelAdmin):
    list_display = ('name', 'distinguished_name', 'parent_distinguished_name', 'updated_at')
    search_fields = ('name', 'distinguished_name', 'parent_distinguished_name')
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
        'identity_type',
        'access_type',
        'inherited',
        'rights',
        'folder',
        'updated_at',
    )
    list_filter = ('identity_type', 'access_type', 'inherited', 'folder__share__file_server')
    search_fields = (
        'identity_sid',
        'identity_name',
        'rights',
        'folder__path',
        'folder__share__unc_path',
        'ad_user__sam_account_name',
        'ad_group__name',
    )
    autocomplete_fields = ('folder', 'ad_user', 'ad_group')
    readonly_fields = ('created_at', 'updated_at')
