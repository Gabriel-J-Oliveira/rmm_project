import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('agents', '0057_admin_terminal_access_grant'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AgentRolloutCoverageRound',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('sequence', models.PositiveIntegerField()),
                ('purpose', models.CharField(choices=[('catch_up', 'Catch-up')], default='catch_up', max_length=20)),
                ('state', models.CharField(choices=[('recorded', 'Recorded')], default='recorded', max_length=20)),
                ('cohort_schema', models.PositiveIntegerField()),
                ('cohort_hash', models.CharField(max_length=64)),
                ('evaluated_at', models.DateTimeField()),
                ('target_snapshot', models.JSONField(default=list)),
                ('reason', models.CharField(max_length=1000)),
                ('total_candidates', models.PositiveIntegerField()),
                ('eligible_count', models.PositiveIntegerField()),
                ('excluded_count', models.PositiveIntegerField()),
                ('already_current_count', models.PositiveIntegerField(default=0)),
                ('reason_summary', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('campaign', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='coverage_rounds', to='agents.agentrolloutcampaign')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stable_coverage_rounds_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['campaign_id', 'sequence']},
        ),
        migrations.AddConstraint(
            model_name='agentrolloutcoverageround',
            constraint=models.UniqueConstraint(fields=('campaign', 'sequence'), name='rollout_coverage_round_sequence'),
        ),
        migrations.AddConstraint(
            model_name='agentrolloutcoverageround',
            constraint=models.CheckConstraint(condition=models.Q(('sequence__gte', 1)), name='rollout_coverage_round_positive'),
        ),
        migrations.AddConstraint(
            model_name='agentrolloutcoverageround',
            constraint=models.CheckConstraint(condition=models.Q(('total_candidates', models.F('eligible_count') + models.F('excluded_count') + models.F('already_current_count'))), name='rollout_coverage_round_counts'),
        ),
    ]
