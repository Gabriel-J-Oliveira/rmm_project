from django.core.exceptions import ValidationError
from django.db import models


class InventoryAgent(models.Model):
    name = models.CharField(max_length=150)
    hostname = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    token_hash = models.CharField(max_length=128, unique=True)
    enabled = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    version = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'hostname']
        indexes = [
            models.Index(fields=['enabled', 'hostname']),
            models.Index(fields=['last_seen_at']),
        ]

    def __str__(self) -> str:
        return f'{self.name} ({self.hostname})'


class InventoryAgentRun(models.Model):
    RUN_FILE_ACL = 'file_acl'
    RUN_AD_INVENTORY = 'ad_inventory'
    RUN_HEARTBEAT = 'heartbeat'
    RUN_TYPE_CHOICES = [
        (RUN_FILE_ACL, 'File ACL'),
        (RUN_AD_INVENTORY, 'AD Inventory'),
        (RUN_HEARTBEAT, 'Heartbeat'),
    ]

    STATUS_RUNNING = 'running'
    STATUS_SUCCESS = 'success'
    STATUS_PARTIAL_SUCCESS = 'partial_success'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_RUNNING, 'Running'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_PARTIAL_SUCCESS, 'Partial success'),
        (STATUS_FAILED, 'Failed'),
    ]

    agent = models.ForeignKey(InventoryAgent, on_delete=models.CASCADE, related_name='runs')
    run_type = models.CharField(max_length=30, choices=RUN_TYPE_CHOICES)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    message = models.TextField(blank=True)
    items_created = models.PositiveIntegerField(default=0)
    items_updated = models.PositiveIntegerField(default=0)
    items_ignored = models.PositiveIntegerField(default=0)
    errors_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['agent', '-started_at']),
            models.Index(fields=['run_type', 'status']),
            models.Index(fields=['status', '-started_at']),
        ]

    def __str__(self) -> str:
        return f'{self.agent} - {self.run_type} - {self.status}'


class ADOrganizationalUnit(models.Model):
    distinguished_name = models.CharField(max_length=1024, unique=True)
    name = models.CharField(max_length=255)
    parent_distinguished_name = models.CharField(max_length=1024, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['distinguished_name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['parent_distinguished_name']),
        ]

    def __str__(self) -> str:
        return self.name or self.distinguished_name


class ADUser(models.Model):
    sid = models.CharField(max_length=255, unique=True)
    sam_account_name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255)
    user_principal_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    distinguished_name = models.CharField(max_length=1024, unique=True, null=True, blank=True)
    ou = models.ForeignKey(
        ADOrganizationalUnit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
    )
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sam_account_name']
        indexes = [
            models.Index(fields=['sam_account_name']),
            models.Index(fields=['display_name']),
            models.Index(fields=['enabled']),
        ]

    def __str__(self) -> str:
        return self.display_name or self.sam_account_name


class ADGroup(models.Model):
    sid = models.CharField(max_length=255, unique=True)
    sam_account_name = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    distinguished_name = models.CharField(max_length=1024, unique=True, null=True, blank=True)
    ou = models.ForeignKey(
        ADOrganizationalUnit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='groups',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['sam_account_name']),
            models.Index(fields=['name']),
        ]

    def __str__(self) -> str:
        return self.name or self.sam_account_name


class ADGroupMembership(models.Model):
    parent_group = models.ForeignKey(
        ADGroup,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    member_user = models.ForeignKey(
        ADUser,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='group_memberships',
    )
    member_group = models.ForeignKey(
        ADGroup,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='parent_memberships',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['parent_group__name', 'member_user__sam_account_name', 'member_group__name']
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(member_user__isnull=False) & models.Q(member_group__isnull=True))
                    | (models.Q(member_user__isnull=True) & models.Q(member_group__isnull=False))
                ),
                name='membership_has_one_member_type',
            ),
            models.UniqueConstraint(
                fields=['parent_group', 'member_user'],
                condition=models.Q(member_user__isnull=False),
                name='unique_parent_group_member_user',
            ),
            models.UniqueConstraint(
                fields=['parent_group', 'member_group'],
                condition=models.Q(member_group__isnull=False),
                name='unique_parent_group_member_group',
            ),
        ]

    def __str__(self) -> str:
        member = self.member_user or self.member_group
        return f'{member} -> {self.parent_group}'

    def clean(self):
        if bool(self.member_user) == bool(self.member_group):
            raise ValidationError('Informe member_user ou member_group, mas nao ambos.')


class FileServer(models.Model):
    name = models.CharField(max_length=255)
    fqdn = models.CharField(max_length=512, blank=True)
    description = models.TextField(blank=True)
    rmm_agent = models.ForeignKey(
        'agents.AgentMachine',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='access_inventory_file_servers',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['name'], name='unique_file_server_name'),
        ]
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['fqdn']),
        ]

    def __str__(self) -> str:
        return self.fqdn or self.name


class Share(models.Model):
    file_server = models.ForeignKey(FileServer, on_delete=models.CASCADE, related_name='shares')
    name = models.CharField(max_length=255)
    unc_path = models.CharField(max_length=1024, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['file_server__name', 'name']
        indexes = [
            models.Index(fields=['file_server', 'name']),
            models.Index(fields=['unc_path']),
        ]

    def __str__(self) -> str:
        return self.unc_path


class Folder(models.Model):
    share = models.ForeignKey(Share, on_delete=models.CASCADE, related_name='folders')
    path = models.CharField(max_length=2048)
    parent_path = models.CharField(max_length=2048, blank=True)
    inheritance_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['share__unc_path', 'path']
        constraints = [
            models.UniqueConstraint(fields=['share', 'path'], name='unique_folder_per_share'),
        ]
        indexes = [
            models.Index(fields=['share', 'path']),
            models.Index(fields=['parent_path']),
            models.Index(fields=['inheritance_enabled']),
        ]

    def __str__(self) -> str:
        return f'{self.share.unc_path}\\{self.path}'.rstrip('\\')


class AclEntry(models.Model):
    IDENTITY_USER = 'user'
    IDENTITY_GROUP = 'group'
    IDENTITY_UNKNOWN = 'unknown'
    IDENTITY_TYPE_CHOICES = [
        (IDENTITY_USER, 'User'),
        (IDENTITY_GROUP, 'Group'),
        (IDENTITY_UNKNOWN, 'Unknown'),
    ]

    ACCESS_ALLOW = 'allow'
    ACCESS_DENY = 'deny'
    ACCESS_TYPE_CHOICES = [
        (ACCESS_ALLOW, 'Allow'),
        (ACCESS_DENY, 'Deny'),
    ]

    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, related_name='acl_entries')
    identity_sid = models.CharField(max_length=255, blank=True)
    identity_name = models.CharField(max_length=512)
    identity_type = models.CharField(max_length=20, choices=IDENTITY_TYPE_CHOICES, default=IDENTITY_UNKNOWN)
    ad_user = models.ForeignKey(
        ADUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='acl_entries',
    )
    ad_group = models.ForeignKey(
        ADGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='acl_entries',
    )
    resolved_ad_user = models.ForeignKey(
        ADUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_acl_entries',
    )
    resolved_ad_group = models.ForeignKey(
        ADGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_acl_entries',
    )
    resolved_identity_type = models.CharField(max_length=20, choices=IDENTITY_TYPE_CHOICES, default=IDENTITY_UNKNOWN)
    resolved_at = models.DateTimeField(null=True, blank=True)
    rights = models.TextField()
    access_type = models.CharField(max_length=10, choices=ACCESS_TYPE_CHOICES, default=ACCESS_ALLOW)
    inherited = models.BooleanField(default=False)
    inheritance_flags = models.CharField(max_length=255, blank=True)
    propagation_flags = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['folder__path', 'identity_name', 'access_type', 'rights']
        constraints = [
            models.UniqueConstraint(
                fields=[
                    'folder',
                    'identity_sid',
                    'identity_name',
                    'rights',
                    'access_type',
                    'inherited',
                    'inheritance_flags',
                    'propagation_flags',
                    'source',
                ],
                name='unique_acl_entry_per_folder_identity_rights',
            ),
        ]
        indexes = [
            models.Index(fields=['identity_sid']),
            models.Index(fields=['identity_name']),
            models.Index(fields=['identity_type']),
            models.Index(fields=['resolved_identity_type']),
            models.Index(fields=['resolved_at']),
            models.Index(fields=['access_type']),
            models.Index(fields=['inherited']),
        ]

    def __str__(self) -> str:
        return f'{self.identity_name} {self.access_type} {self.rights}'
