from django.test import TestCase, override_settings
from dashboard.views import operational_action_availability
from agents.test_administrative_actions import AdministrativeActionTests


class ActionAvailabilityTests(AdministrativeActionTests):
    def test_availability_honors_flags_scopes_and_capabilities(self):
        self.machine.status = 'online'
        self.machine.save(update_fields=['status'])
        availability = operational_action_availability(self.machine, self.operator)
        self.assertTrue(availability['start_service']['enabled'])
        self.assertEqual(availability['start_service']['services'], ['AcmePilotService'])
        self.assertTrue(availability['gpupdate']['enabled'])
        self.assertFalse(availability['query_service']['enabled'])
        with override_settings(NIGHTOWL_ADMIN_ACTIONS_ENABLED=False):
            self.assertFalse(operational_action_availability(self.machine, self.operator)['gpupdate']['enabled'])
        with override_settings(NIGHTOWL_ADMIN_ACTION_SCOPES={}):
            self.assertFalse(operational_action_availability(self.machine, self.operator)['start_service']['enabled'])
        self.machine.status = 'offline'
        self.assertFalse(operational_action_availability(self.machine, self.operator)['gpupdate']['enabled'])
