from django.db import migrations, models
import django.db.models.deletion


def mark_existing_as_desk(apps, schema_editor):
    NotificationOutbox = apps.get_model('tickets', 'NotificationOutbox')
    NotificationOutbox.objects.update(source_app='desk')


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0007_notificationoutbox'),
    ]

    operations = [
        migrations.RenameField(
            model_name='notificationoutbox',
            old_name='body',
            new_name='body_text',
        ),
        migrations.AddField(
            model_name='notificationoutbox',
            name='source_app',
            field=models.CharField(
                choices=[
                    ('desk', 'Desk'),
                    ('rmm', 'RMM'),
                    ('system', 'Sistema'),
                    ('gmud', 'GMUD'),
                    ('auth', 'Autenticacao'),
                ],
                default='system',
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name='notificationoutbox',
            name='source_model',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='notificationoutbox',
            name='source_id',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.RunPython(mark_existing_as_desk, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='notificationoutbox',
            name='ticket',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='notification_outbox',
                to='tickets.ticket',
            ),
        ),
        migrations.AddField(
            model_name='notificationoutbox',
            name='cc',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='notificationoutbox',
            name='bcc',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='notificationoutbox',
            name='body_html',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='notificationoutbox',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pendente'),
                    ('sending', 'Enviando'),
                    ('sent', 'Enviada'),
                    ('skipped', 'Ignorada'),
                    ('failed', 'Falhou'),
                    ('cancelled', 'Cancelada'),
                ],
                default='pending',
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name='notificationoutbox',
            name='priority',
            field=models.CharField(
                choices=[('low', 'Baixa'), ('normal', 'Normal'), ('high', 'Alta')],
                default='normal',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='notificationoutbox',
            name='attempts',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='notificationoutbox',
            name='max_attempts',
            field=models.PositiveIntegerField(default=3),
        ),
        migrations.AddField(
            model_name='notificationoutbox',
            name='last_error',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='notificationoutbox',
            name='last_attempt_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='notificationoutbox',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddIndex(
            model_name='notificationoutbox',
            index=models.Index(fields=['source_app', 'created_at'], name='tickets_not_source__783751_idx'),
        ),
        migrations.AddIndex(
            model_name='notificationoutbox',
            index=models.Index(fields=['priority', 'created_at'], name='tickets_not_priorit_c835da_idx'),
        ),
    ]
