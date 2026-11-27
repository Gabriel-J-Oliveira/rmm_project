from django.test import SimpleTestCase

from .software_execution_profiles import ExecutionProfileError, normalize_execution_profile


def exe_operation(method, *, uninstall=False):
    value = {
        'method': method,
        'arguments': ['/vendor switch', 'value with spaces'],
        'timeout_seconds': 120,
        'accepted_exit_codes': [0, 3010],
        'reboot_exit_codes': [3010],
    }
    if uninstall:
        value['executable_path'] = r'%ProgramFiles%\Vendor\App\uninstall.exe'
    return value


def exe_profile(**overrides):
    profile = {
        'schema_version': 2,
        'install': exe_operation('exe'),
        'update': None,
        'uninstall': None,
        'detection': {
            'method': 'registry_value', 'hive': 'HKLM', 'view': 'registry64',
            'key': r'SOFTWARE\Vendor\Product', 'value_name': 'DisplayVersion',
        },
        'expected_version': '4.2.0',
        'reboot_behavior': 'defer_to_policy',
    }
    profile.update(overrides)
    return profile


class ExeExecutionProfileTests(SimpleTestCase):
    def test_each_operation_is_independent_and_optional(self):
        install_only = normalize_execution_profile(exe_profile(), 'exe')
        self.assertIsNotNone(install_only['install'])
        self.assertIsNone(install_only['update'])
        self.assertIsNone(install_only['uninstall'])

        update_only = normalize_execution_profile(exe_profile(
            install=None, update=exe_operation('vendor_exe'),
        ), 'exe')
        self.assertIsNone(update_only['install'])
        self.assertIsNotNone(update_only['update'])

        uninstall_only = normalize_execution_profile(exe_profile(
            install=None, uninstall=exe_operation('vendor_exe', uninstall=True),
            expected_version='', detection={
                'method': 'windows_service', 'service_name': 'VendorAgent', 'expected_status': 'any',
            },
        ), 'exe')
        self.assertIsNone(uninstall_only['install'])
        self.assertIsNotNone(uninstall_only['uninstall'])

    def test_structured_arguments_timeout_and_reboot_codes_are_normalized(self):
        normalized = normalize_execution_profile(exe_profile(), 'exe')
        self.assertEqual(normalized['install']['arguments'], ['/vendor switch', 'value with spaces'])
        self.assertEqual(normalized['install']['accepted_exit_codes'], [0, 3010])
        self.assertEqual(normalized['install']['reboot_exit_codes'], [3010])

    def test_empty_operation_profile_is_rejected(self):
        with self.assertRaisesMessage(ExecutionProfileError, 'operation_profile_empty'):
            normalize_execution_profile(exe_profile(install=None), 'exe')

    def test_timeout_and_exit_1641_are_rejected(self):
        for operation in (
            exe_operation('exe') | {'timeout_seconds': 29},
            exe_operation('exe') | {'timeout_seconds': 1801},
            exe_operation('exe') | {'accepted_exit_codes': [0, 1641], 'reboot_exit_codes': []},
        ):
            with self.subTest(operation=operation), self.assertRaises(ExecutionProfileError):
                normalize_execution_profile(exe_profile(install=operation), 'exe')

    def test_service_detection_cannot_claim_update_version(self):
        with self.assertRaisesMessage(ExecutionProfileError, 'update_version_unverifiable'):
            normalize_execution_profile(exe_profile(
                install=None, update=exe_operation('vendor_exe'),
                detection={
                    'method': 'windows_service', 'service_name': 'VendorAgent', 'expected_status': 'running',
                },
            ), 'exe')

    def test_uninstall_never_accepts_inventory_string_or_user_local_path(self):
        for path in (
            r'%ProgramFiles%\Vendor\App\uninstall.exe /S',
            r'%LOCALAPPDATA%\Vendor\uninstall.exe',
            r'%ProgramData%\..\Windows\uninstall.exe',
        ):
            with self.subTest(path=path), self.assertRaises(ExecutionProfileError):
                normalize_execution_profile(exe_profile(
                    install=None, uninstall=exe_operation('vendor_exe', uninstall=True) | {'executable_path': path},
                    expected_version='', detection={
                        'method': 'windows_service', 'service_name': 'VendorAgent', 'expected_status': 'any',
                    },
                ), 'exe')

    def test_detection_identity_is_bounded_for_the_strict_ack_contract(self):
        key = 'SOFTWARE' + ''.join('\\' + ('V' * 128) for _ in range(4))
        with self.assertRaises(ExecutionProfileError):
            normalize_execution_profile(exe_profile(detection={
                'method': 'registry_value', 'hive': 'HKLM', 'view': 'registry64',
                'key': key, 'value_name': 'DisplayVersion',
            }), 'exe')

    def test_msi_schema_one_contract_remains_unchanged(self):
        product = '{11111111-2222-3333-4444-555555555555}'
        profile = {
            'schema_version': 1,
            'install': {'method': 'msi', 'arguments': [], 'product_code': product},
            'update': {'method': 'msi_upgrade', 'arguments': []},
            'uninstall': None,
            'detection': {'method': 'msi_product_code', 'product_code': product},
            'expected_version': '1.0', 'reboot_behavior': 'report_only',
        }
        normalized = normalize_execution_profile(profile, 'msi')
        self.assertEqual(normalized['schema_version'], profile['schema_version'])
        self.assertEqual(normalized['install']['method'], profile['install']['method'])
        self.assertEqual(normalized['install']['arguments'], profile['install']['arguments'])
        self.assertEqual(normalized['install']['product_code'], product.strip('{}').upper())
        self.assertEqual(normalized['update'], profile['update'])
        self.assertEqual(normalized['uninstall'], profile['uninstall'])
        self.assertEqual(normalized['detection'], profile['detection'] | {
            'product_code': product.strip('{}').upper(),
        })
        self.assertEqual(normalized['expected_version'], profile['expected_version'])
        self.assertEqual(normalized['reboot_behavior'], profile['reboot_behavior'])
