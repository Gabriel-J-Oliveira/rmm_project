from django.db import migrations


def set_campaign_guard(apps, schema_editor, *, reverse=False):
    Campaign = apps.get_model('agents', 'AgentRolloutCampaign')
    Wave = apps.get_model('agents', 'AgentRolloutWave')
    campaign = Campaign._meta.db_table
    wave = Wave._meta.db_table
    pg = schema_editor.connection.vendor == 'postgresql'
    name = 'rollout_guard_campaign_terminal'
    if pg:
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {name} ON {campaign}')
        schema_editor.execute(f'DROP FUNCTION IF EXISTS {name}()')
    else:
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {name}')
    anchor = "(OLD.state='draft' AND NEW.state='completed' AND NEW.approved_by_id IS NOT NULL AND NEW.eligible_count=0 AND NEW.total_candidates=NEW.excluded_count AND NEW.current_wave_id IS NULL AND NOT EXISTS (SELECT 1 FROM {wave} WHERE campaign_id=NEW.id) AND {stable})"
    stable = "NEW.release_snapshot ->> 'stable_desktop_profile' = 'true'" if pg else "json_extract(NEW.release_snapshot, '$.stable_desktop_profile') = 1"
    anchor = anchor.format(wave=wave, stable=stable) if not reverse else '0=1'
    normal = "((OLD.state='draft' AND NEW.state IN ('ready','aborted')) OR (OLD.state='ready' AND NEW.state IN ('running','aborted')) OR (OLD.state='running' AND NEW.state IN ('paused','completed','aborted')) OR (OLD.state='paused' AND NEW.state IN ('running','aborted')))"
    condition = f"OLD.state <> NEW.state AND NOT ({normal} OR {anchor})"
    if pg:
        schema_editor.execute(f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {condition} THEN RAISE EXCEPTION '{name}' USING ERRCODE='23514'; END IF; RETURN NEW; END $$")
        schema_editor.execute(f'CREATE TRIGGER {name} BEFORE UPDATE ON {campaign} FOR EACH ROW EXECUTE FUNCTION {name}()')
    else:
        schema_editor.execute(f"CREATE TRIGGER {name} BEFORE UPDATE ON {campaign} WHEN {condition} BEGIN SELECT RAISE(ABORT,'{name}'); END")


def forward(apps, schema_editor):
    set_campaign_guard(apps, schema_editor)


def reverse(apps, schema_editor):
    set_campaign_guard(apps, schema_editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [('agents', '0065_stable_retry_ready_outcome')]
    operations = [migrations.RunPython(forward, reverse)]
