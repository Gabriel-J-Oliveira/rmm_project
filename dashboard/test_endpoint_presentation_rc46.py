from types import SimpleNamespace

from django.test import SimpleTestCase

from dashboard.views import defender_filter_state, defender_state, detail_defender_state, infer_endpoint_type


class EndpointPresentationTests(SimpleTestCase):
    def snapshot(self, system):
        return SimpleNamespace(raw_payload={'collections': {'system': system}})

    def test_product_type_takes_precedence_over_names(self):
        self.assertEqual(infer_endpoint_type('ERP-01', 'Windows 11 Enterprise Evaluation', self.snapshot({'product_type': 1, 'machine_type': 'workstation'})), 'workstation')
        self.assertEqual(infer_endpoint_type('PC-01', 'Windows 11', self.snapshot({'product_type': 3})), 'server')
        self.assertEqual(infer_endpoint_type('PC-01', '', self.snapshot({'product_type': 1, 'machine_type': 'notebook'})), 'notebook')

    def test_legacy_observation_and_safe_fallback(self):
        self.assertEqual(infer_endpoint_type('ERP-01', 'Windows 11 Enterprise', self.snapshot({'os_name': 'Windows 11', 'machine_type': 'workstation'})), 'workstation')
        self.assertEqual(infer_endpoint_type('ERP-01', 'Windows 11 Enterprise'), 'workstation')
        self.assertEqual(infer_endpoint_type('PC-01', 'Windows Server 2019'), 'server')
        self.assertEqual(infer_endpoint_type('ERP-01', ''), 'unknown')

    def test_defender_observations_do_not_conflate_states(self):
        cases = [
            ({'enabled': True, 'real_time_protection_enabled': True}, 'Protegido', 'ok'),
            ({'enabled': False, 'real_time_protection_enabled': False, 'raw': {'defender_present': True}}, 'Inativo ou parcial', 'attention'),
            ({'raw': {'state': 'not_present'}}, 'Ausente', 'attention'),
            ({'enabled': False, 'real_time_protection_enabled': False}, 'Desconhecido', 'unknown'),
            ({'raw': {'defender_present': False}}, 'Desconhecido', 'unknown'),
            ({'enabled': True, 'real_time_protection_enabled': True, 'raw': {'defender_present': False}}, 'Desconhecido', 'unknown'),
            ({'enabled': False, 'real_time_protection_enabled': False, 'raw': {'defender_present': False, 'raw': {'error': 'failed'}}}, 'Erro de coleta', 'unknown'),
            ({}, 'Desconhecido', 'unknown'),
            ({'enabled': None, 'real_time_protection_enabled': None}, 'Desconhecido', 'unknown'),
        ]
        for payload, label, key in cases:
            with self.subTest(label=label):
                self.assertEqual(defender_state(payload)['label'], label)
                self.assertEqual(defender_filter_state(payload), key)
                self.assertEqual(detail_defender_state(payload, [{'name': 'Bitdefender'}])['key'], key)
