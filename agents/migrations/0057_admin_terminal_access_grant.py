from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0056_rc49_seed_alert_policies'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AdminTerminalAccessGrant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=True)),
                ('machine_id', models.CharField(max_length=255)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('reason', models.CharField(max_length=500)),
                ('approval_ref', models.CharField(max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='admin_terminal_grants_created', to=settings.AUTH_USER_MODEL)),
                ('endpoint', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='agents.agentmachine')),
                ('operator', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='admin_terminal_grants', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='adminterminalaccessgrant',
            constraint=models.UniqueConstraint(fields=('operator', 'endpoint'), name='admin_terminal_grant_operator_endpoint'),
        ),
        migrations.AlterModelOptions(
            name='terminalsession',
            options={'permissions': [
                ('open_terminalsession', 'Open synthetic remote terminal transport'),
                ('open_admin_terminalsession', 'Open administrative remote terminal session'),
                ('manage_admin_terminal_grant', 'Grant and revoke administrative remote terminal access'),
            ]},
        ),
    ]
