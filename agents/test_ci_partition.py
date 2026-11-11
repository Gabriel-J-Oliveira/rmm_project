"""Keep every Windows-dependent integration test in the required CI gate."""
from django.test import SimpleTestCase
from django.test.runner import DiscoverRunner
from types import SimpleNamespace
from pathlib import Path

from agents.ci_runner import WindowsE2ERunner, WindowsBrowserE2ERunner


WINDOWS_E2E = {
    'agents.test_automation_protocol_e2e.AutomationProtocolE2ETests.test_calendar_occurrence_uses_existing_dotnet_execution_and_exact_receipt',
    'agents.test_automation_protocol_e2e.AutomationProtocolE2ETests.test_actual_worker_capability_allows_immediate_and_recurring_first_occurrence',
    'agents.test_conpty_sessions.ConPtyTransportTests.test_real_powershell_stream_over_websocket_and_disconnect',
    'agents.test_conpty_sessions.ConPtyTransportTests.test_native_commands_resolve_without_inheriting_parent_environment',
    'agents.test_jobs_protocol_e2e.JobsProtocolE2ETests.test_real_dotnet_dispatch_start_result_and_dashboard',
    'agents.test_operational_actions.OperationalActionE2ETests.test_native_query_through_dotnet_runtime_result_and_ack',
    'agents.test_script_library.ScriptLibraryE2ETests.test_actual_powershell_diagnostic_v2_persisted_with_exact_ack',
    'agents.test_telemetry_protocol_e2e.TelemetryProtocolE2ETests.test_dotnet_windows_and_live_points_via_real_http',
    'agents.test_terminal_browser.TerminalBrowserSmokeTests.test_real_browser_real_pty_simulated_local_consent_click',
    'agents.test_terminal_transport.TerminalTransportTests.test_native_dotnet_loopback_synthetic_echo',
    'agents.test_terminal_admin_auth.AdminAuthIntegrationTests.test_simulated_browser_real_backend_agent_and_windows_rejection',
}


def flatten(suite):
    for entry in suite:
        if hasattr(entry, 'id'):
            yield entry
        else:
            yield from flatten(entry)


class WindowsE2EPartitionTests(SimpleTestCase):
    def test_isolated_browser_script_runs_execution_policy_browser_e2e(self):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'Invoke-WindowsE2E.ps1'
        content = script.read_text(encoding='utf-8')
        self.assertIn("agents.test_automation.AutomationBrowserTests.test_real_ui_and_persisted_automations", content)
        self.assertIn("$env:NIGHTOWL_TEST_BROWSER_E2E = '1'", content)
        self.assertIn("NIGHTOWL_TEST_CHROME", content)

    def test_isolated_browser_gate_fails_for_skipped_or_missing_tests(self):
        runner = WindowsBrowserE2ERunner(verbosity=0)
        result = SimpleNamespace(failures=[], errors=[], unexpectedSuccesses=[], skipped=[], testsRun=1)
        self.assertEqual(runner.suite_result(None, result), 0)
        result.testsRun = 0
        self.assertNotEqual(runner.suite_result(None, result), 0)
        result.testsRun = 1
        result.skipped = [('test', 'reason')]
        self.assertNotEqual(runner.suite_result(None, result), 0)

    def test_every_windows_e2e_is_selected_for_its_required_job(self):
        runner = DiscoverRunner(tags={'windows_e2e'}, verbosity=0)
        actual = {test.id() for test in flatten(runner.build_suite(['agents']))}
        self.assertEqual(actual, WINDOWS_E2E)

    def test_gate_fails_for_skipped_or_missing_tests(self):
        runner = WindowsE2ERunner(verbosity=0)
        result = SimpleNamespace(failures=[], errors=[], unexpectedSuccesses=[], skipped=[], testsRun=len(WINDOWS_E2E))
        self.assertEqual(runner.suite_result(None, result), 0)
        result.testsRun -= 1
        self.assertNotEqual(runner.suite_result(None, result), 0)
        result.testsRun += 1
        result.skipped = [('test', 'reason')]
        self.assertNotEqual(runner.suite_result(None, result), 0)
