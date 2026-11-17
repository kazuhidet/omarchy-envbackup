import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('wizard', Path(__file__).parents[1] / 'restore-wizard.py')
w = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'
        self.work.mkdir(mode=0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_multiple_selection_skips_instead_of_error(self):
        for value in ('', '\n'):
            with self.subTest(output=value), patch.object(w.subprocess, 'run', return_value=
                    subprocess.CompletedProcess([], 0, stdout=value)):
                self.assertEqual(w.choose('header', ['etc/example.conf'], True), [])

    def test_selection_preserves_spaces_and_unicode_line_separator(self):
        values = ['etc/with spaces.conf', 'etc/unicode\u2028name.conf']
        with patch.object(w.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 0, stdout='\n'.join(values) + '\n')) as execute:
            self.assertEqual(w.choose('header', values, True), values)
        self.assertEqual(execute.call_args.kwargs['input'], '\n'.join(values))
        self.assertIn('--output-delimiter', execute.call_args.args[0])

    def test_unknown_nonempty_selection_is_still_rejected(self):
        with patch.object(w.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 0, stdout='etc/unexpected.conf\n')):
            with self.assertRaises(ValueError):
                w.choose('header', ['etc/example.conf'], True)

    def test_system_empty_selection_changes_no_live_files(self):
        archive = self.work / 'system.tar'
        with tarfile.open(archive, 'w') as tar:
            tar.addfile(tarfile.TarInfo('etc/example.conf'))
        with patch.object(w.os, 'geteuid', return_value=0), \
             patch.object(w.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='\n')), \
             patch.object(w, 'install_file') as install:
            w.restore_system(archive, self.work)
        self.assertEqual(json.loads((self.work / 'system-plan.json').read_text()), [])
        install.assert_not_called()

    def test_protected_system_and_paths(self):
        for name in ('/etc/test', '../etc/test', 'etc/../root/a', 'etc/fstab',
                     'etc/machine-id', 'etc/sudoers.d/custom', 'etc/passwd',
                     'etc/modprobe.d/audio.conf', 'etc/ssh/ssh_host_key',
                     'var/lib/j715/config', 'boot/boot.bin', 'usr/share/alsa/a',
                     'etc/systemd/system/a.service', 'etc/invalid\nname'):
            with self.subTest(name=name):
                self.assertFalse(w.system_allowed(name))
        for name in ('etc/application/settings.conf', 'usr/local/bin/example', 'opt/app/file'):
            self.assertTrue(w.system_allowed(name))

    def test_existing_file_and_link_saved_before_replace(self):
        source = self.root / 'source'
        source.write_text('new')
        target = self.root / 'target'
        target.write_text('original')
        backup = self.work / 'original' / 'target'
        w.copy_leaf(source, target, backup, self.work)
        self.assertEqual(target.read_text(), 'new')
        self.assertEqual(backup.read_text(), 'original')
        events = [json.loads(line) for line in (self.work / 'journal.jsonl').read_text().splitlines()]
        self.assertEqual([e['status'] for e in events], ['pending', 'done'])
        linked = self.root / 'linked'
        linked.symlink_to(source)
        w.copy_leaf(source, linked, self.work / 'original' / 'linked', self.work)
        self.assertFalse(linked.is_symlink())
        self.assertEqual(source.read_text(), 'new')
        self.assertTrue((self.work / 'original' / 'linked').is_symlink())

    def test_destination_parent_symlink_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.root / 'link'
        link.symlink_to(outside, target_is_directory=True)
        source = self.root / 'source'
        source.write_text('data')
        with self.assertRaises(ValueError):
            w.copy_leaf(source, link / 'new', self.work / 'backup', self.work)
        self.assertFalse((outside / 'new').exists())

    def test_failure_keeps_original(self):
        target = self.root / 'target'
        target.write_text('original')
        def fail(_):
            raise OSError('disk full')
        with self.assertRaises(OSError):
            w.install_file(target, self.work / 'saved', self.work, fail)
        self.assertEqual(target.read_text(), 'original')
        self.assertEqual((self.work / 'saved').read_text(), 'original')
        self.assertEqual(list(self.root.glob('.envbackup-*')), [])

    def test_existing_fifo_rejected_before_copy(self):
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        source = self.root / 'source'
        source.write_text('new')
        with self.assertRaises(ValueError):
            w.copy_leaf(source, fifo, self.work / 'saved', self.work)
        self.assertFalse((self.work / 'saved').exists())

    def test_home_maps_user_preserves_protected_and_empty_directories(self):
        source = self.root / 'old-home'
        home = self.root / 'new-home'
        home.mkdir()
        for name in ('.config/envbackup/config.sh', '.local/share/keyrings/login.keyring',
                     'tool/app', 'Documents/file'):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('snapshot')
        (source / 'Empty').mkdir()
        (home / 'Documents').mkdir()
        (home / 'Documents/file').write_text('fresh install')
        with patch.object(w, 'choose', side_effect=lambda _, values, multiple=False: values), \
             patch.object(w, 'confirm', return_value=True):
            w.restore_home(source, home, self.work, ['tool'])
        self.assertEqual((home / 'Documents/file').read_text(), 'snapshot')
        self.assertEqual((self.work / 'original-home/Documents/file').read_text(), 'fresh install')
        self.assertTrue((home / 'Empty').is_dir())
        self.assertFalse((home / '.config/envbackup').exists())
        self.assertFalse((home / '.local/share/keyrings').exists())
        self.assertFalse((home / 'tool').exists())

    def test_cancel_home_changes_nothing(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'file').write_text('data')
        home = self.root / 'home'
        home.mkdir()
        with patch.object(w, 'choose', return_value=['file']), patch.object(w, 'confirm', return_value=False):
            w.restore_home(source, home, self.work)
        self.assertEqual(list(home.iterdir()), [])

    def test_walk_permission_error_is_not_silently_skipped(self):
        error = PermissionError('unreadable directory')
        def failed_walk(*args, **kwargs):
            kwargs['onerror'](error)
            return iter(())
        with patch.object(w.os, 'walk', side_effect=failed_walk):
            with self.assertRaises(PermissionError):
                list(w.leaves(self.root))

    def test_home_owner_and_attributes_are_applied_after_chown(self):
        source = self.root / 'source'
        source.write_text('data')
        source.chmod(0o640)
        target = self.root / 'target'
        events = []
        original_copystat = w.shutil.copystat
        def attributes(src, dst, **kwargs):
            events.append('attributes')
            return original_copystat(src, dst, **kwargs)
        with patch.object(w, 'can_preserve_owner', return_value=True), \
             patch.object(w.os, 'chown', side_effect=lambda *a, **k: events.append('owner')), \
             patch.object(w.shutil, 'copystat', side_effect=attributes):
            w.copy_leaf(source, target, self.work / 'original', self.work)
        self.assertLess(events.index('owner'), len(events) - 1)
        self.assertEqual(events[-1], 'attributes')
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_snapshot_selection_includes_old_host_and_filters_system(self):
        snapshots = [dict(id='old-home', time='2026-10-20', hostname='old', tags=['home']),
                     dict(id='old-system', time='2026-10-20', hostname='old', tags=['system']),
                     dict(id='other', time='2026-10-21', hostname='new', tags=['system'])]
        with patch.object(w, 'choose', side_effect=lambda _, values: values[0]):
            home = w.snapshot_choice(snapshots, 'home')
            self.assertEqual(home['hostname'], 'old')
            self.assertEqual(w.snapshot_choice(snapshots, 'system', home['hostname'])['id'], 'old-system')

    def test_package_plan_excludes_kernel_unavailable_and_aur(self):
        lists = self.root / 'lists'
        lists.mkdir()
        (lists / 'pacman-explicit-native.txt').write_text('linux-asahi\ninstalled\nmissing\ngone\n')
        (lists / 'pacman-explicit-foreign-aur.txt').write_text('aur-package\n')
        commands = []
        with patch.object(w, 'output', side_effect=['installed\n', 'missing\nlinux-asahi\n']), \
             patch.object(w, 'choose', side_effect=lambda _, values, multiple: values), \
             patch.object(w, 'confirm', return_value=True), \
             patch.object(w, 'run', side_effect=lambda args: commands.append(args)):
            w.restore_packages(lists, self.work)
        self.assertEqual(commands, [['sudo', 'pacman', '-S', '--needed', '--', 'missing']])
        self.assertEqual((self.work / 'unavailable-packages.txt').read_text(), 'gone\n')
        self.assertEqual((self.work / 'aur-packages.txt').read_text(), 'aur-package\n')

    def test_bad_package_arguments_rejected(self):
        path = self.root / 'packages'
        path.write_text('--root=/tmp/other\n')
        with self.assertRaises(ValueError):
            w.package_names(path)

    def test_system_archive_filters_and_requires_per_file_confirmation(self):
        archive = self.work / 'system.tar'
        with tarfile.open(archive, 'w') as tar:
            for name in ('etc/fstab', 'etc/example.conf', '../outside', 'boot/file'):
                info = tarfile.TarInfo(name)
                info.size = 3
                tar.addfile(info, io.BytesIO(b'new'))
        selected = []
        def select(_, values, multiple):
            selected.extend(values)
            return values
        with patch.object(w.os, 'geteuid', return_value=0), \
             patch.object(w, 'choose', side_effect=select), \
             patch.object(w, 'member_owner', return_value=(0, 0)), \
             patch.object(w, 'preview'), patch.object(w, 'confirm', return_value=False), \
             patch.object(w, 'install_file') as install:
            w.restore_system(archive, self.work)
        self.assertEqual(selected, ['etc/example.conf'])
        install.assert_not_called()

    def test_system_apply_reads_content_without_extracting_archive_links(self):
        archive = self.work / 'system.tar'
        with tarfile.open(archive, 'w') as tar:
            info = tarfile.TarInfo('etc/envbackup-test.conf')
            info.size = 7
            info.mode = 0o640
            tar.addfile(info, io.BytesIO(b'content'))
        captured = []
        def install(target, backup, work, writer, mode, owner, mtime):
            tmp = self.work / 'written'
            tmp.touch()
            writer(tmp)
            captured.append((str(target), tmp.read_bytes(), mode, owner))
        with patch.object(w.os, 'geteuid', return_value=0), \
             patch.object(w, 'choose', side_effect=lambda _, values, multiple: values), \
             patch.object(w, 'member_owner', return_value=(0, 0)), \
             patch.object(w, 'preview'), patch.object(w, 'confirm', return_value=True), \
             patch.object(w, 'install_file', side_effect=install):
            w.restore_system(archive, self.work)
        self.assertEqual(captured, [('/etc/envbackup-test.conf', b'content', 0o640, (0, 0))])

    def test_duplicate_system_archive_path_rejected(self):
        archive = self.work / 'system.tar'
        with tarfile.open(archive, 'w') as tar:
            for _ in range(2):
                tar.addfile(tarfile.TarInfo('etc/example.conf'))
        with patch.object(w.os, 'geteuid', return_value=0), self.assertRaises(ValueError):
            w.restore_system(archive, self.work)

    @unittest.skipUnless(shutil.which('restic'), 'restic is required')
    def test_real_restic_repository_staging_and_copy(self):
        repo = self.root / 'repo'
        fixture = self.root / 'fixture'
        fixture.mkdir()
        (fixture / 'document').write_text('backed up')
        env = dict(os.environ, RESTIC_PASSWORD='test-password', RESTIC_CACHE_DIR=str(self.root / 'cache'))
        env.pop('RESTIC_PASSWORD_COMMAND', None)
        def restic(*args):
            result = subprocess.run(['restic', '-r', str(repo), *map(str, args)], env=env,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout
        restic('init')
        restic('backup', '--tag', 'home', fixture)
        snap = json.loads(restic('snapshots', '--json'))[0]
        stage = self.work / 'stage'
        restic('restore', snap['id'] + ':' + str(fixture), '--target', stage, '--verify')
        source = stage
        home = self.root / 'restored'
        home.mkdir()
        with patch.object(w, 'choose', side_effect=lambda _, values, multiple: values), \
             patch.object(w, 'confirm', return_value=True):
            w.restore_home(source, home, self.work)
        self.assertEqual((home / 'document').read_text(), 'backed up')


if __name__ == '__main__':
    unittest.main()
