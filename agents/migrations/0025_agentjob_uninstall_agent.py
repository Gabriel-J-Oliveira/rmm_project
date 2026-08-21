from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0024_agent_release_trust_bundle'),
    ]

    operations = [
        migrations.AlterField(
            model_name='agentjob',
            name='job_type',
            field=models.CharField(choices=[('force_inventory', 'Force inventory'), ('collect_disks', 'Collect disks'), ('collect_security', 'Collect security'), ('collect_software', 'Collect software'), ('ping', 'Ping'), ('collect_logs', 'Collect logs'), ('windows_update_scan', 'Windows Update scan'), ('update_agent', 'Update agent'), ('update_trusted_release_keys', 'Update trusted release keys'), ('uninstall_agent', 'Uninstall agent'), ('restart_agent', 'Restart agent')], max_length=80),
        ),
    ]
