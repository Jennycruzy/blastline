from __future__ import annotations

import unittest

from blastline.ingest.lockfiles import parse_pnpm_lock, pnpm_key_parts


class PnpmLockfileTest(unittest.TestCase):
    def test_pnpm_key_parts_supports_legacy_and_current_keys(self) -> None:
        cases = {
            "/lodash/4.17.21": ("lodash", "4.17.21"),
            "/is-extglob@2.1.1": ("is-extglob", "2.1.1"),
            "/@scope/package/1.2.3": ("@scope/package", "1.2.3"),
            "/@scope/package@1.2.3": ("@scope/package", "1.2.3"),
            "/foo/1.0.0_bar@2.0.0": ("foo", "1.0.0"),
            "foo@1.0.0(bar@2.0.0)": ("foo", "1.0.0"),
            "'@scope/package@1.2.3(peer@4.5.6)'": ("@scope/package", "1.2.3"),
        }
        for key, expected in cases.items():
            with self.subTest(key=key):
                self.assertEqual(pnpm_key_parts(key), expected)

    def test_parse_pnpm_lock_does_not_report_valid_key_formats_as_issues(self) -> None:
        body = b"""\
lockfileVersion: '9.0'

packages:
  /legacy/1.0.0:
    dependencies:
      dep: 2.0.0
  '@scope/current@3.0.0(peer@4.0.0)':
    dependencies:
      '@quoted/dep': 2.0.0

snapshots:
  /legacy/1.0.0:
    dependencies:
      dep: 2.0.0
  '@scope/current@3.0.0(peer@4.0.0)': {}
"""

        result = parse_pnpm_lock(body, "npm")

        self.assertEqual(result.issues, ())
        self.assertEqual(
            {(item.package_name, item.version) for item in result.resolutions},
            {("legacy", "1.0.0"), ("@scope/current", "3.0.0")},
        )
        scope_resolution = next(item for item in result.resolutions if item.package_name == "@scope/current")
        self.assertEqual(tuple(item.name for item in scope_resolution.dependencies), ("@quoted/dep",))


if __name__ == "__main__":
    unittest.main()
