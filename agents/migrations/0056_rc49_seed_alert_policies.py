from django.db import migrations


def seed_policies(apps, schema_editor):
    policy = apps.get_model('agents', 'AlertPolicy')
    presets = [
        ('cpu', 'Uso de CPU', 5, 80, [(85, 15, 80), (95, 30, 90)]),
        ('ram', 'Uso de RAM', 5, 80, [(85, 15, 80), (95, 30, 90)]),
        ('disk', 'Ocupação de disco', 1440, None, [(85, 0, 80), (95, 0, 90)]),
        ('offline', 'Tempo offline', None, None, [(24, 0, 0), (72, 0, 0)]),
        ('uptime', 'Uptime', 1440, None, [(15, 0, 14), (30, 0, 29)]),
        ('antivirus', 'Antivírus', 1440, None, [(None, 0, None), (None, 0, None)]),
        ('job_failures', 'Falhas de jobs', None, None, [(None, 0, None), (None, 0, None)]),
    ]
    for metric, label, age, coverage, rules in presets:
        for (severity, level), (threshold, duration, recovery) in zip(
            [('warning', 'Atenção'), ('critical', 'Crítico')], rules, strict=True,
        ):
            policy.objects.using(schema_editor.connection.alias).get_or_create(
                key=f'{metric}-{severity}',
                defaults={
                    'name': f'{label} — {level}', 'metric': metric, 'severity': severity,
                    'threshold': threshold, 'duration_minutes': duration, 'recovery_threshold': recovery,
                    'max_age_minutes': age, 'min_coverage_percent': coverage,
                },
            )


class Migration(migrations.Migration):
    dependencies = [('agents', '0055_rc49_alert_policy')]

    operations = [migrations.RunPython(seed_policies, migrations.RunPython.noop)]
