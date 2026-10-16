from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('dashboard', '0004_remoteinstallbatch_remoteinstallbatchitem')]

    operations = [
        migrations.AddField(
            model_name='remoteinstallbatchitem',
            name='target_ad_sid',
            field=models.CharField(max_length=184, blank=True, default=''),
        ),
    ]
