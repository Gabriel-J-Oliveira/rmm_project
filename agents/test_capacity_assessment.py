import copy
import json

from django.test import TestCase

from .telemetry_diagnostics import build_capacity_assessment


def diagnostics(cpu='NO_PRESSURE_OBSERVED', memory='NO_PRESSURE_OBSERVED', duration=86400):
    return {
        'schema_version': 1,
        'window': {
            'start': '2026-09-30T00:00:00+00:00',
            'end': '2026-10-01T00:00:00+00:00',
            'duration_seconds': duration,
        },
        'diagnostics': {
            'cpu': {'status': cpu},
            'memory': {'status': memory},
        },
    }


def hardware():
    return {
        'cpu': {
            'name': 'Synthetic CPU',
            'physical_cores': 8,
            'logical_processors': 16,
            'max_clock_mhz': 3200,
        },
        'memory_total_bytes': 17179869184,
        'memory': {
            'total_bytes': 17179869184,
            'slots_total': 2,
            'slots_used': 1,
            'slots_free': 1,
            'modules': [{'capacity_bytes': 17179869184}],
        },
    }


class CapacityAssessmentTests(TestCase):
    def test_both_no_pressure_and_pure_json_safe(self):
        primary = diagnostics()
        context = diagnostics(duration=604800)
        with self.assertNumQueries(0):
            result = build_capacity_assessment(primary, context, hardware())
        self.assertEqual(result['capacity']['cpu']['status'], 'NO_PRESSURE_OBSERVED')
        self.assertEqual(result['capacity']['memory']['status'], 'NO_PRESSURE_OBSERVED')
        self.assertEqual(result['windows'], {'primary': primary['window'], 'context': context['window']})
        json.dumps(result)

    def test_real_like_24h_primary_with_partial_7d_context(self):
        result = build_capacity_assessment(diagnostics(),
                                           diagnostics(cpu='NOT_EVALUATED', memory='NOT_EVALUATED',
                                                       duration=604800))
        for resource in ('cpu', 'memory'):
            self.assertEqual(result['capacity'][resource]['status'], 'NO_PRESSURE_OBSERVED')
            self.assertIn('context_not_evaluated', result['capacity'][resource]['reasons'])

    def test_primary_pressure_needs_context_corroboration(self):
        primary = diagnostics(cpu='PRESSURE', memory='PRESSURE')
        for context_status in ('PRESSURE', 'ELEVATED'):
            with self.subTest(context_status=context_status):
                context = diagnostics(cpu=context_status, memory=context_status, duration=172800)
                result = build_capacity_assessment(primary, context)
                for resource in ('cpu', 'memory'):
                    item = result['capacity'][resource]
                    self.assertEqual(item['status'], 'SUSTAINED_PRESSURE')
                    self.assertEqual(item['reasons'], ['primary_pressure', 'context_corroborates_pressure'])

    def test_optional_context_never_yields_sustained_pressure(self):
        cases = (
            ('NOT_EVALUATED', 'NOT_EVALUATED'),
            ('NO_PRESSURE_OBSERVED', 'NO_PRESSURE_OBSERVED'),
            ('SPIKY', 'OBSERVE'),
            ('ELEVATED', 'OBSERVE'),
            ('PRESSURE', 'OBSERVE'),
        )
        for primary_status, expected in cases:
            with self.subTest(primary_status=primary_status):
                result = build_capacity_assessment(diagnostics(cpu=primary_status, memory=primary_status))
                self.assertIsNone(result['windows']['context'])
                self.assertEqual(result['capacity']['cpu']['status'], expected)
                self.assertEqual(result['capacity']['memory']['status'], expected)
                if primary_status in ('SPIKY', 'ELEVATED', 'PRESSURE'):
                    self.assertIn('context_window_unavailable', result['capacity']['cpu']['reasons'])

    def test_context_without_corroboration(self):
        primary = diagnostics(cpu='PRESSURE', memory='PRESSURE')
        for context_status in ('NO_PRESSURE_OBSERVED', 'SPIKY', 'NOT_EVALUATED'):
            with self.subTest(context_status=context_status):
                context = diagnostics(cpu=context_status, memory=context_status, duration=172800)
                result = build_capacity_assessment(primary, context)
                self.assertEqual(result['capacity']['cpu']['status'], 'OBSERVE')
                self.assertEqual(result['capacity']['memory']['status'], 'OBSERVE')
                expected_reason = ('context_not_evaluated' if context_status == 'NOT_EVALUATED'
                                   else 'context_not_corroborated')
                self.assertIn(expected_reason, result['capacity']['cpu']['reasons'])

    def test_elevated_and_spiky_primary_never_become_sustained(self):
        context = diagnostics(cpu='PRESSURE', memory='PRESSURE', duration=172800)
        for primary_status, reason in (('ELEVATED', 'primary_elevated'), ('SPIKY', 'primary_spiky')):
            with self.subTest(primary_status=primary_status):
                primary = diagnostics(cpu=primary_status, memory=primary_status)
                result = build_capacity_assessment(primary, context)
                self.assertEqual(result['capacity']['cpu']['status'], 'OBSERVE')
                self.assertEqual(result['capacity']['memory']['status'], 'OBSERVE')
                self.assertIn(reason, result['capacity']['cpu']['reasons'])

    def test_current_no_pressure_with_historical_pressure_needs_observation(self):
        result = build_capacity_assessment(diagnostics(),
                                           diagnostics(cpu='PRESSURE', memory='PRESSURE', duration=172800))
        for resource in ('cpu', 'memory'):
            self.assertEqual(result['capacity'][resource]['status'], 'OBSERVE')
            self.assertEqual(result['capacity'][resource]['reasons'], ['historical_signal_present'])

    def test_context_cannot_replace_invalid_primary(self):
        result = build_capacity_assessment(
            diagnostics(cpu='NOT_EVALUATED', memory='NOT_EVALUATED'),
            diagnostics(cpu='PRESSURE', memory='PRESSURE', duration=172800),
        )
        for resource in ('cpu', 'memory'):
            self.assertEqual(result['capacity'][resource]['status'], 'NOT_EVALUATED')
            self.assertEqual(result['capacity'][resource]['reasons'], ['primary_not_evaluated'])

    def test_cpu_and_memory_assessments_are_independent(self):
        result = build_capacity_assessment(
            diagnostics(cpu='PRESSURE', memory='NO_PRESSURE_OBSERVED'),
            diagnostics(cpu='PRESSURE', memory='NO_PRESSURE_OBSERVED', duration=172800),
        )
        self.assertEqual(result['capacity']['cpu']['status'], 'SUSTAINED_PRESSURE')
        self.assertEqual(result['capacity']['memory']['status'], 'NO_PRESSURE_OBSERVED')
        self.assertNotIn('overall', result['capacity'])

    def test_context_duration_must_not_be_shorter(self):
        primary = diagnostics(duration=86400)
        with self.assertRaises(ValueError):
            build_capacity_assessment(primary, diagnostics(duration=3600))
        same = diagnostics(duration=86400)
        same['window']['end'] = '2026-10-02T00:00:00+00:00'
        result = build_capacity_assessment(primary, same)
        self.assertEqual(result['windows']['context'], same['window'])

    def test_hardware_facts_are_preserved(self):
        result = build_capacity_assessment(diagnostics(), hardware=hardware())
        cpu = result['hardware_context']['cpu']
        memory = result['hardware_context']['memory']
        self.assertEqual(cpu, {
            'status': 'AVAILABLE', 'name': 'Synthetic CPU', 'physical_cores': 8,
            'logical_processors': 16, 'max_clock_mhz': 3200, 'socket_count': None,
        })
        self.assertEqual(memory, {
            'status': 'AVAILABLE', 'total_bytes': 17179869184, 'slots_total': 2,
            'slots_used': 1, 'slots_free': 1, 'module_count': 1,
        })

    def test_hardware_missing_does_not_downgrade_telemetry_assessment(self):
        result = build_capacity_assessment(diagnostics(cpu='PRESSURE'),
                                           diagnostics(cpu='PRESSURE', duration=172800))
        self.assertEqual(result['capacity']['cpu']['status'], 'SUSTAINED_PRESSURE')
        self.assertEqual(result['hardware_context']['cpu']['status'], 'MISSING')
        self.assertEqual(result['hardware_context']['memory']['status'], 'MISSING')

    def test_hardware_partial_per_resource(self):
        cpu_only = build_capacity_assessment(diagnostics(), hardware={'cpu': hardware()['cpu']})
        self.assertEqual(cpu_only['hardware_context']['cpu']['status'], 'AVAILABLE')
        self.assertEqual(cpu_only['hardware_context']['memory']['status'], 'MISSING')
        memory_only = build_capacity_assessment(diagnostics(), hardware={'memory': hardware()['memory']})
        self.assertEqual(memory_only['hardware_context']['cpu']['status'], 'MISSING')
        self.assertEqual(memory_only['hardware_context']['memory']['status'], 'AVAILABLE')
        partial = build_capacity_assessment(diagnostics(), hardware={
            'cpu': {'name': 'Synthetic CPU'},
            'memory': {'total_bytes': 17179869184},
        })
        self.assertEqual(partial['hardware_context']['cpu']['status'], 'PARTIAL')
        self.assertEqual(partial['hardware_context']['memory']['status'], 'PARTIAL')

    def test_memory_totals_and_slots_must_agree(self):
        data = hardware()
        data['memory_total_bytes'] += 1
        with self.assertRaises(ValueError):
            build_capacity_assessment(diagnostics(), hardware=data)
        data = hardware()
        data['memory']['slots_used'] = 2
        with self.assertRaises(ValueError):
            build_capacity_assessment(diagnostics(), hardware=data)

    def test_invalid_hardware_values_are_rejected(self):
        for field, value in (('physical_cores', -1), ('logical_processors', True),
                             ('max_clock_mhz', float('nan')), ('socket_count', '2')):
            with self.subTest(field=field):
                data = hardware()
                data['cpu'][field] = value
                with self.assertRaises(ValueError):
                    build_capacity_assessment(diagnostics(), hardware=data)
        for field, value in (('total_bytes', -1), ('slots_total', '2'), ('modules', {})):
            with self.subTest(field=field):
                data = hardware()
                data['memory'][field] = value
                with self.assertRaises(ValueError):
                    build_capacity_assessment(diagnostics(), hardware=data)

    def test_invalid_primary_or_context_contract_is_rejected(self):
        for value in (None, {}, {'schema_version': 2}, diagnostics(cpu='TYPO'),
                      diagnostics(cpu=[])):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_capacity_assessment(value)
        for value in ({}, diagnostics(memory='TYPO'), diagnostics(memory=[])):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_capacity_assessment(diagnostics(), value)
        invalid_window = diagnostics()
        invalid_window['window']['duration_seconds'] = 0
        with self.assertRaises(ValueError):
            build_capacity_assessment(invalid_window)

    def test_input_is_not_mutated(self):
        primary = diagnostics(cpu='PRESSURE')
        context = diagnostics(cpu='ELEVATED', duration=172800)
        hw = hardware()
        before = copy.deepcopy((primary, context, hw))
        build_capacity_assessment(primary, context, hw)
        self.assertEqual((primary, context, hw), before)
