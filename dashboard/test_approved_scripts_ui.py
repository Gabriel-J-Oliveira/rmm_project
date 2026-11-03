from django.test import override_settings
from django.urls import reverse
from agents.test_script_library import ScriptLibraryTests
from agents.script_library import DEMO


class ApprovedScriptsPageTests(ScriptLibraryTests):
    def test_page_hint_respects_flags_identity_revocation_and_capability(self):
        self.machine.status = 'online'
        self.machine.save(update_fields=['status'])
        key = f'{DEMO.script_id}:{DEMO.version}:{DEMO.sha256}'
        url = reverse('endpoint-detail', args=[self.machine.pk])
        self.assertTrue(self.client.get(url).context['approved_script_availability'][key])
        with override_settings(NIGHTOWL_APPROVED_SCRIPTS_ENABLED=False):
            self.assertFalse(self.client.get(url).context['approved_script_availability'][key])
        with override_settings(NIGHTOWL_REVOKED_SCRIPT_VERSIONS=[f'{DEMO.script_id}:{DEMO.version}']):
            self.assertFalse(self.client.get(url).context['approved_script_availability'][key])
        self.snapshot.raw_payload['agent']['capabilities'] = []
        self.snapshot.save(update_fields=['raw_payload'])
        self.assertFalse(self.client.get(url).context['approved_script_availability'][key])
