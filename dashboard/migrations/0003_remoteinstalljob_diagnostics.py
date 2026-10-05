from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('dashboard', '0002_remoteinstalljob')]
    operations = [migrations.AddField(
        model_name='remoteinstalljob', name='diagnostics',
        field=models.JSONField(blank=True, default=dict),
    )]
