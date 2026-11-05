"""Fail the Windows integration gate when tests are skipped or omitted."""
from django.test.runner import DiscoverRunner


class WindowsE2ERunner(DiscoverRunner):
    def suite_result(self, suite, result, **kwargs):
        from agents.test_ci_partition import WINDOWS_E2E
        failures = super().suite_result(suite, result, **kwargs)
        if result.skipped or result.testsRun != len(WINDOWS_E2E):
            self.log('Windows E2E gate requires every listed test to run without skips.', level='ERROR')
            return failures + 1
        return failures
