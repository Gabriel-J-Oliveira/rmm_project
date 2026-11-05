import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('agents', '0036_configure_telemetry_job'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name='endpointperformancesample', name='schema_version', field=models.PositiveSmallIntegerField(default=1, db_default=1)),
        migrations.AddField(model_name='endpointperformancesample', name='resolution_seconds', field=models.PositiveIntegerField(default=300, db_default=300)),
        migrations.AddField(model_name='endpointperformancesample', name='window_end_at', field=models.DateTimeField(null=True)),
        migrations.AddField(model_name='endpointperformancesample', name='metric_summaries', field=models.JSONField(default=dict, db_default={})),
        migrations.AlterField(model_name='endpointperformancesample', name='uptime_seconds', field=models.BigIntegerField(null=True)),
        migrations.AddConstraint(model_name='endpointperformancesample', constraint=models.UniqueConstraint(fields=('endpoint', 'collected_at', 'resolution_seconds'), condition=models.Q(schema_version=2), name='unique_endpoint_window_resolution')),
        migrations.CreateModel(name='TelemetryLiveSession', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('session_id', models.UUIDField(default=uuid.uuid4)),
            ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('expires_at', models.DateTimeField()), ('hard_expires_at', models.DateTimeField()),
            ('endpoint', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='live_telemetry_session', to='agents.agentmachine')),
            ('operator', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
    ]
