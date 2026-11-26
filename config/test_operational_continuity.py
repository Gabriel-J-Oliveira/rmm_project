import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class ContinuityChecks(SimpleTestCase):
    def test_compare_reports_changed_setting_without_values(self):
        with TemporaryDirectory() as root:
            baseline = Path(root) / 'before.json'
            output = io.StringIO()
            call_command('check_operational_continuity', settings_only=True, stdout=output)
            baseline.write_text(output.getvalue(), encoding='utf-8')
            call_command('check_operational_continuity', compare=baseline, settings_only=True, stdout=io.StringIO())
            self.assertIsInstance(json.loads(output.getvalue())['values']['NIGHTOWL_ADMIN_ACTION_SCOPES'], str)
            with override_settings(NIGHTOWL_ADMIN_ACTION_SCOPES={'synthetic': {'gpupdate': True}}):
                with self.assertRaisesRegex(CommandError, 'changed: NIGHTOWL_ADMIN_ACTION_SCOPES'):
                    call_command('check_operational_continuity', compare=baseline, settings_only=True)
