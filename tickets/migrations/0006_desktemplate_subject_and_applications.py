from django.db import migrations, models


APPLICATION_MAP = {
    'Composer publico': 'composer_publico',
    'Composer público': 'composer_publico',
    'Composer interno': 'composer_interno',
    'Resolver chamado': 'resolver_chamado',
    'Escalar chamado': 'escalar_chamado',
    'Automacao: chamado criado': 'automacao_chamado_criado',
    'Automação: chamado criado': 'automacao_chamado_criado',
    'Automacao: chamado resolvido': 'automacao_chamado_resolvido',
    'Automação: chamado resolvido': 'automacao_chamado_resolvido',
    'Automacao: chamado reaberto': 'automacao_chamado_reaberto',
    'Automação: chamado reaberto': 'automacao_chamado_reaberto',
    'Automacao: aguardando solicitante': 'automacao_aguardando_solicitante',
    'Automação: aguardando solicitante': 'automacao_aguardando_solicitante',
}


def normalize_applications(apps, schema_editor):
    DeskTemplate = apps.get_model('tickets', 'DeskTemplate')
    for old_value, new_value in APPLICATION_MAP.items():
        DeskTemplate.objects.filter(application=old_value).update(application=new_value)


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0005_ticketcategory_subcategories'),
    ]

    operations = [
        migrations.AddField(
            model_name='desktemplate',
            name='subject',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.RunPython(normalize_applications, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='desktemplate',
            name='application',
            field=models.CharField(
                blank=True,
                choices=[
                    ('composer_publico', 'Composer publico'),
                    ('composer_interno', 'Composer interno'),
                    ('resolver_chamado', 'Resolver chamado'),
                    ('escalar_chamado', 'Escalar chamado'),
                    ('automacao_chamado_criado', 'Automacao: chamado criado'),
                    ('automacao_chamado_resolvido', 'Automacao: chamado resolvido'),
                    ('automacao_chamado_reaberto', 'Automacao: chamado reaberto'),
                    ('automacao_aguardando_solicitante', 'Automacao: aguardando solicitante'),
                ],
                max_length=120,
            ),
        ),
    ]
