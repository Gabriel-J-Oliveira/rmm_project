from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0047_persistedtelemetryanalysis_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='terminalaccessgrant',
            name='allow_admin_auth',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterModelOptions(
            name='terminalsession',
            options={
                'permissions': [
                    ('open_terminalsession', 'Open synthetic remote terminal transport'),
                    ('open_admin_terminalsession', 'Open administrative remote terminal session'),
                ],
            },
        ),
    ]
