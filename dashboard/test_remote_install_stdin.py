import os
import subprocess
from unittest import mock, skipUnless

from django.test import SimpleTestCase

from dashboard import remote_install as runner
from dashboard.test_remote_install import COMPUTER, CONTRACT, SENTINEL


class RemoteWrapperStdinTests(SimpleTestCase):
    def dispatch(self, protocol, script, events=None):
        with mock.patch.object(runner, '_new_winrm_protocol', return_value=protocol):
            return runner._remote_script(COMPUTER['fqdn'], 'synthetic-admin', SENTINEL,
                                         script, 30, on_event=events)

    def protocol(self):
        protocol = mock.Mock()
        protocol.open_shell.return_value = 'shell'
        protocol.run_command.return_value = 'command'
        protocol.get_command_output_raw.return_value = (b'', b'', 0, True)
        return protocol

    def test_large_real_wrapper_uses_bytes_stdin_and_short_argv(self):
        protocol = self.protocol()
        script = runner._installer_script(CONTRACT) + '\n#' + 'x' * 12000
        self.dispatch(protocol, script)
        protocol.run_command.assert_called_once_with('shell', 'powershell.exe',
                                                    ['-NoProfile', '-NonInteractive', '-Command', '-'])
        args, kwargs = protocol.send_command_input.call_args
        self.assertEqual(args[:2], ('shell', 'command'))
        self.assertIsInstance(args[2], bytes)
        self.assertIn(script.encode('ascii'), args[2])
        self.assertEqual(kwargs, {'end': True})
        self.assertNotIn(SENTINEL.encode(), args[2])
        self.assertNotIn(b'synthetic-admin', args[2])
        self.assertNotIn('-EncodedCommand', repr(protocol.run_command.call_args))
        self.assertNotIn(SENTINEL, repr(protocol.run_command.call_args))

    def test_fragmented_frames_still_parsed_for_large_script(self):
        for outputs, expected in (
                ([(b'NIGHTOWL_INSTALL:NOT_', b'', None, False),
                  (b'STARTED:26\n', b'', 26, True)], [('NOT_STARTED', 26)]),
                ([(b'NIGHTOWL_INSTALL:STA', b'', None, False),
                  (b'RTED\nNIGHTOWL_INSTALL:FIN', b'', None, False),
                  (b'ISHED:0\n', b'', 0, True)], [('STARTED', None), ('FINISHED', 0)])):
            with self.subTest(events=expected):
                protocol = self.protocol()
                protocol.get_command_output_raw.side_effect = outputs
                events = []
                self.dispatch(protocol, '#' + 'x' * 15000, lambda event, code: events.append((event, code)))
                self.assertEqual(events, expected)

    def test_empty_non_ascii_and_oversized_scripts_fail_before_connection(self):
        for script in ('', '\u00e9', 'x' * runner.MAX_REMOTE_SCRIPT_BYTES):
            with self.subTest(size=len(script)), mock.patch.object(runner, '_new_winrm_protocol') as connect:
                with self.assertRaises(runner.InstallFailure) as raised:
                    runner._remote_script(COMPUTER['fqdn'], 'admin', SENTINEL, script, 30)
                self.assertEqual(raised.exception.code, 'REMOTE_SCRIPT_INVALID')
                self.assertFalse(raised.exception.command_attempted)
                connect.assert_not_called()

    def test_open_shell_and_run_command_failures_are_retry_safe(self):
        for method in ('open_shell', 'run_command'):
            with self.subTest(method=method):
                protocol = self.protocol()
                getattr(protocol, method).side_effect = RuntimeError(SENTINEL)
                with self.assertRaises(runner.InstallFailure) as raised:
                    self.dispatch(protocol, 'exit 0')
                self.assertFalse(raised.exception.command_attempted)
                self.assertNotIn(SENTINEL, str(raised.exception))
                protocol.send_command_input.assert_not_called()

    def test_missing_command_id_never_sends_script(self):
        protocol = self.protocol()
        protocol.run_command.return_value = None
        with self.assertRaises(runner.InstallFailure) as raised:
            self.dispatch(protocol, 'exit 0')
        self.assertFalse(raised.exception.command_attempted)
        protocol.send_command_input.assert_not_called()

    def test_ambiguous_stdin_send_is_fail_closed_and_cleanup_runs(self):
        protocol = self.protocol()
        protocol.send_command_input.side_effect = RuntimeError(SENTINEL)
        with self.assertRaises(runner.InstallFailure) as raised:
            self.dispatch(protocol, 'exit 0')
        self.assertTrue(raised.exception.command_attempted)
        self.assertNotIn(SENTINEL, str(raised.exception))
        protocol.get_command_output_raw.assert_not_called()
        protocol.cleanup_command.assert_called_once_with('shell', 'command')
        protocol.close_shell.assert_called_once_with('shell')

    @skipUnless(os.name == 'nt', 'Requires local Windows PowerShell; no WinRM or installer')
    def test_windows_powershell_executes_long_multiline_stdin_without_installer(self):
        harmless = ('#' + 'x' * 15000 + '\n'
                    'try {\nWrite-Output "NIGHTOWL_INSTALL:NOT_STARTED:26"\n'
                    '} catch { exit 99 }\nexit 26')
        protocol = self.protocol()
        self.dispatch(protocol, harmless)
        payload = protocol.send_command_input.call_args.args[2]
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', '-'],
                                input=payload, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 26)
        self.assertIn(b'NIGHTOWL_INSTALL:NOT_STARTED:26', result.stdout)
        self.assertEqual(result.stderr, b'')
