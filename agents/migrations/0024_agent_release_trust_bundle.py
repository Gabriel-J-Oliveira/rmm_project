import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('agents', '0023_agent_release_signing_key'),
    ]

    operations = [
        migrations.CreateModel(
            name='AgentReleaseRootKey',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('root_key_id', models.CharField(max_length=120, unique=True)),
                ('algorithm', models.CharField(default='RSA-PSS-SHA256', max_length=40)),
                ('public_key_xml', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('revoked', 'Revoked'), ('retired', 'Retired')], db_index=True, default='active', max_length=20)),
                ('valid_from', models.DateTimeField(blank=True, null=True)),
                ('valid_until', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('revocation_reason', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['root_key_id'],
            },
        ),
        migrations.CreateModel(
            name='AgentReleaseTrustBundle',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('bundle_version', models.PositiveBigIntegerField(unique=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('published', 'Published'), ('revoked', 'Revoked'), ('superseded', 'Superseded')], db_index=True, default='draft', max_length=20)),
                ('schema_version', models.PositiveSmallIntegerField(default=1)),
                ('root_key_id', models.CharField(db_index=True, max_length=120)),
                ('bundle_url', models.URLField(max_length=1000)),
                ('signature_url', models.URLField(max_length=1000)),
                ('metadata_url', models.URLField(max_length=1000)),
                ('bundle_sha256', models.CharField(max_length=64)),
                ('signature_sha256', models.CharField(max_length=64)),
                ('size', models.BigIntegerField(default=0)),
                ('generated_at', models.DateTimeField(blank=True, null=True)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('valid_from', models.DateTimeField(blank=True, null=True)),
                ('valid_until', models.DateTimeField(blank=True, null=True)),
                ('active_key_ids', models.JSONField(blank=True, default=list)),
                ('revoked_key_ids', models.JSONField(blank=True, default=list)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('revocation_reason', models.TextField(blank=True)),
                ('superseded_at', models.DateTimeField(blank=True, null=True)),
                ('superseded_reason', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_agent_release_trust_bundles', to=settings.AUTH_USER_MODEL)),
                ('replacement_bundle', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='replaced_trust_bundles', to='agents.agentreleasetrustbundle')),
            ],
            options={
                'ordering': ['-bundle_version'],
            },
        ),
        migrations.AlterField(
            model_name='agentjob',
            name='job_type',
            field=models.CharField(choices=[('force_inventory', 'Force inventory'), ('collect_disks', 'Collect disks'), ('collect_security', 'Collect security'), ('collect_software', 'Collect software'), ('ping', 'Ping'), ('collect_logs', 'Collect logs'), ('windows_update_scan', 'Windows Update scan'), ('update_agent', 'Update agent'), ('update_trusted_release_keys', 'Update trusted release keys'), ('restart_agent', 'Restart agent')], max_length=80),
        ),
        migrations.AddIndex(
            model_name='agentreleaserootkey',
            index=models.Index(fields=['root_key_id', 'status'], name='agents_agen_root_ke_1b449c_idx'),
        ),
        migrations.AddIndex(
            model_name='agentreleaserootkey',
            index=models.Index(fields=['status', 'valid_from', 'valid_until'], name='agents_agen_status_e0f6b9_idx'),
        ),
        migrations.AddIndex(
            model_name='agentreleasetrustbundle',
            index=models.Index(fields=['status', '-bundle_version'], name='agents_agen_status_3a9462_idx'),
        ),
        migrations.AddIndex(
            model_name='agentreleasetrustbundle',
            index=models.Index(fields=['root_key_id', 'status'], name='agents_agen_root_ke_c1f0f2_idx'),
        ),
        migrations.AddIndex(
            model_name='agentreleasetrustbundle',
            index=models.Index(fields=['bundle_sha256'], name='agents_agen_bundle__68959c_idx'),
        ),
    ]
