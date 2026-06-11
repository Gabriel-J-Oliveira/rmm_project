from django.db import migrations


def create_categories(apps, schema_editor):
    TicketCategory = apps.get_model('tickets', 'TicketCategory')
    categories = [
        ('Acesso', '#22C55E', 'Solicitacoes de acesso, contas e permissoes.'),
        ('Hardware', '#38BDF8', 'Equipamentos, pecas e perifericos.'),
        ('Software', '#A78BFA', 'Aplicativos, licencas e instalacoes.'),
        ('Rede', '#10B981', 'Conectividade, Wi-Fi, VPN e links.'),
        ('Impressora', '#F59E0B', 'Impressoras, filas e suprimentos.'),
        ('Servidor', '#818CF8', 'Servidores e servicos internos.'),
        ('Seguranca', '#EF4444', 'Antivirus, acesso remoto e eventos de seguranca.'),
        ('RMM / Alerta', '#C084FC', 'Chamados originados do monitoramento.'),
        ('Solicitacao', '#94A3B8', 'Solicitacoes gerais da TI.'),
    ]
    for name, color, description in categories:
        TicketCategory.objects.get_or_create(
            name=name,
            defaults={'color': color, 'description': description, 'is_active': True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_categories, migrations.RunPython.noop),
    ]
