"""Run as root after installing helpers. Writes only a newly created /tmp tree."""
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def main():
    if os.geteuid() != 0:
        raise SystemExit('root is required')
    account = pwd.getpwnam('kazu')
    other = pwd.getpwnam('nobody')
    root = Path(tempfile.mkdtemp(prefix='envbackup-restore-test-'))
    root.chmod(0o755)
    try:
        for name in ('repo', 'cache', 'work', 'destination', 'bin'):
            path = root / name
            path.mkdir(mode=0o700)
            os.chown(path, account.pw_uid, account.pw_gid)
        fixture = root / 'fixture'
        fixture.mkdir(mode=0o750)
        secret = fixture / 'mode-zero'
        secret.write_bytes(b'root-owned fixture\n')
        os.setxattr(secret, 'user.envbackup-test', b'attribute')
        secret.chmod(0)
        foreign = fixture / 'foreign'
        foreign.write_bytes(b'foreign-owned fixture\n')
        os.chown(foreign, other.pw_uid, other.pw_gid)
        foreign.chmod(0o600)
        subdir = fixture / 'private'
        subdir.mkdir(mode=0o700)
        (subdir / 'nested').write_text('nested')
        subdir.chmod(0)
        env = {'HOME': account.pw_dir, 'PATH': '/usr/bin:/bin',
               'RESTIC_PASSWORD': 'temporary-test-only', 'RESTIC_CACHE_DIR': str(root / 'cache')}
        sudo_env = {'PATH': '/usr/bin:/bin', 'SUDO_UID': str(account.pw_uid)}
        user = ['/usr/bin/setpriv', f'--reuid={account.pw_uid}', f'--regid={account.pw_gid}',
                '--init-groups', '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs']
        repo = root / 'repo'
        run([*user, '/usr/bin/restic', '-r', repo, 'init'], env=env, stdout=subprocess.DEVNULL)
        run(['/usr/local/libexec/envbackup-read', 'backup', '-r', repo, fixture],
            env=sudo_env, input=json.dumps(env), text=True)
        helper = '/usr/local/libexec/envbackup-restore-home'
        stage = root / 'work/home'
        run([helper, 'restore', '-r', repo, 'latest:' + str(fixture),
             '--target', stage, '--verify'], env=sudo_env, input=json.dumps(env), text=True)
        gum = root / 'bin/gum'
        gum.write_text('#!/usr/bin/python3\nimport sys\nif sys.argv[1] == "choose":\n print(sys.stdin.read(), end="")\n')
        gum.chmod(0o755)
        destination = root / 'destination'
        original = destination / 'mode-zero'
        original.write_bytes(b'original root-only\n')
        original.chmod(0)
        env['PATH'] = str(root / 'bin') + ':/usr/bin:/bin'
        run([helper, 'apply-home', '--apply-home', stage, '--home', destination,
             '--work', root / 'work'], env=sudo_env, input=json.dumps(env), text=True)
        for name in ('mode-zero', 'foreign', 'private', 'private/nested'):
            source = fixture / name
            for base in (stage, destination):
                restored = base / name
                a, b = source.stat(), restored.stat()
                assert (a.st_uid, a.st_gid, a.st_mode & 0o7777) == (b.st_uid, b.st_gid, b.st_mode & 0o7777), name
                if source.is_file():
                    assert source.read_bytes() == restored.read_bytes(), name
        assert os.getxattr(destination / 'mode-zero', 'user.envbackup-test') == b'attribute'
        saved = root / 'work/original-home/mode-zero'
        assert saved.read_bytes() == b'original root-only\n'
        assert saved.stat().st_uid == 0 and saved.stat().st_mode & 0o777 == 0
        print('PASS: privileged stage/verify/apply, root and foreign ownership, mode 000, nested directories, xattr, original backup')
    finally:
        shutil.rmtree(root)


if __name__ == '__main__':
    main()
