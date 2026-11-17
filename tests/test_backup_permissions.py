import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
LOADER = importlib.machinery.SourceFileLoader('read_helper', str(ROOT / 'envbackup-read'))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
helper = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(helper)


class ExecCaptured(Exception):
    pass


class PermissionTests(unittest.TestCase):
    def test_root_drops_uid_and_keeps_only_read_capability(self):
        account = pwd.getpwuid(os.getuid())
        with patch.object(helper.os, 'geteuid', return_value=0), \
             patch.dict(os.environ, {'SUDO_UID': str(account.pw_uid), 'LD_PRELOAD': 'untrusted'}), \
             patch.object(helper.sys, 'argv', ['envbackup-read', 'backup', '-r', 'repo', '/home/test']), \
             patch.object(helper.os, 'execve', side_effect=ExecCaptured) as execute:
            with self.assertRaises(ExecCaptured):
                helper.launch()
        executable, args, env = execute.call_args.args
        self.assertEqual(executable, '/usr/bin/setpriv')
        self.assertIn(f'--reuid={account.pw_uid}', args)
        self.assertIn('--bounding-set=-all,+dac_read_search', args)
        self.assertIn('--no-new-privs', args)
        self.assertNotIn('LD_PRELOAD', env)
        self.assertNotIn('RESTIC_PASSWORD', env)

    def test_environment_reaches_restic_only_after_uid_drop(self):
        env = {'HOME': '/home/test', 'DBUS_SESSION_BUS_ADDRESS': 'test-session',
               'SSH_AUTH_SOCK': 'test-agent', 'RESTIC_PASSWORD_COMMAND': 'secret-tool lookup service envbackup',
               'RESTIC_PASSWORD': 'test-secret'}
        caps = dict.fromkeys(('CapEff', 'CapPrm', 'CapInh', 'CapAmb', 'CapBnd'), 4)
        with patch.object(helper.os, 'geteuid', return_value=1000), \
             patch.object(helper.sys, 'argv', ['helper', '--as-user', 'backup', '/home/test']), \
             patch.object(helper, 'capability_sets', return_value=caps), \
             patch.object(helper.sys, 'stdin', io.StringIO(json.dumps(env))), \
             patch.object(helper.os, 'open', return_value=9), \
             patch.object(helper.os, 'dup2'), patch.object(helper.os, 'close'), \
             patch.object(helper.os, 'execve', side_effect=ExecCaptured) as execute:
            with self.assertRaises(ExecCaptured):
                helper.launch()
        executable, args, actual = execute.call_args.args
        self.assertEqual(executable, '/usr/bin/restic')
        self.assertNotIn('test-secret', args)
        for key, value in env.items():
            self.assertEqual(actual[key], value)

    def test_missing_read_capability_is_fatal(self):
        with patch.object(helper.os, 'geteuid', return_value=1000), \
             patch.object(helper.sys, 'argv', ['helper', '--as-user', '--check']), \
             patch.object(helper, 'capability_sets', return_value={'CapEff': 0}):
            with self.assertRaises(ValueError):
                helper.launch()

    def test_backup_policy_cannot_run_restore(self):
        caps = dict.fromkeys(('CapEff', 'CapPrm', 'CapInh', 'CapAmb', 'CapBnd'), 4)
        with patch.object(helper.os, 'geteuid', return_value=1000), \
             patch.object(helper.sys, 'argv', ['helper', '--as-user', 'restore', 'snapshot']), \
             patch.object(helper, 'capability_sets', return_value=caps):
            with self.assertRaises(ValueError):
                helper.launch()

    def shell(self, extra):
        with tempfile.TemporaryDirectory() as folder:
            conf = Path(folder) / 'config.sh'
            conf.write_text((ROOT / 'config.default.sh').read_text())
            log = Path(folder) / 'commands'
            bootstrap = (ROOT / 'envbackup').read_text().split('\ncase "${1:-menu}"')[0]
            result = subprocess.run(['bash', '-c', bootstrap + '\n' + extra],
                                    env=dict(os.environ, ENVBACKUP_CONFIG=str(conf), TEST_COMMAND_LOG=str(log)),
                                    text=True, capture_output=True)
            return result, log.read_text() if log.exists() else ''

    def test_partial_snapshot_is_not_success(self):
        result, _ = self.shell('restic() { return 3; }; rs backup "$HOME"')
        self.assertEqual(result.returncode, 3)
        self.assertIn('不完全', result.stderr)

    def test_partial_home_still_saves_system_and_cache_but_never_prunes(self):
        result, commands = self.shell('''
say() { :; }
rs_read() {
  printf 'read %s\\n' "$*" >> "$TEST_COMMAND_LOG"
  if [[ " $* " == *" --tag home "* ]]; then return 3; fi
}
rs() { printf 'normal %s\\n' "$*" >> "$TEST_COMMAND_LOG"; }
backup_one usb test-repo
''')
        self.assertEqual(result.returncode, 1)
        self.assertIn('--tag home', commands)
        self.assertIn('--tag system', commands)
        self.assertIn('--tag pkgcache', commands)
        self.assertNotIn('forget', commands)

    def test_complete_backup_prunes(self):
        result, commands = self.shell('''
say() { :; }
rs_read() { printf 'read %s\\n' "$*" >> "$TEST_COMMAND_LOG"; }
rs() { printf 'normal %s\\n' "$*" >> "$TEST_COMMAND_LOG"; }
backup_one usb test-repo
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('forget', commands)


if __name__ == '__main__':
    unittest.main()
