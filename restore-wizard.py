#!/usr/bin/env python3
"""Interactive restore of an envbackup repository. Never invoked by a timer."""
import argparse
import difflib
import grp
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile


# Keep installation identity, boot/hardware, authentication and package management.
SYSTEM_PROTECTED = (
    'etc/fstab', 'etc/crypttab', 'etc/machine-id', 'etc/m4-install-complete',
    'etc/hostname', 'etc/hosts', 'etc/passwd', 'etc/shadow', 'etc/group',
    'etc/gshadow', 'etc/subuid', 'etc/subgid', 'etc/sudoers', 'etc/sudoers.d',
    'etc/pam.d', 'etc/security', 'etc/ssh', 'etc/ssl', 'etc/ca-certificates',
    'etc/NetworkManager', 'etc/iwd', 'etc/systemd/network', 'etc/resolv.conf',
    'etc/pacman.conf', 'etc/pacman.d', 'etc/mkinitcpio.conf', 'etc/mkinitcpio.d',
    'etc/dracut.conf', 'etc/dracut.conf.d', 'etc/kernel', 'etc/default/grub',
    'etc/grub.d', 'etc/modules-load.d', 'etc/modprobe.d', 'etc/udev',
    'etc/asound.conf', 'etc/alsa', 'etc/pipewire', 'etc/wireplumber',
    'etc/systemd/system', 'etc/systemd/user',
)
SYSTEM_ALLOWED = ('etc', 'opt', 'usr/local', 'root')
# Device pairing, fingerprints and J715 state in var/lib are intentionally excluded.
HOME_PROTECTED = (
    '.cache', '.local/share/Trash', '.local/share/keyrings', '.config/envbackup',
    '.local/state/envbackup', 'envbackup-restore', 'envbackup-mount',
    '.config/systemd/user', '.local/bin/envbackup',
)


class Cancelled(Exception):
    pass


HOME_HELPER = '/usr/local/libexec/envbackup-restore-home'


def privileged_home(operation, *args):
    # Only the already-dropped user process reads this environment, never root.
    run(['sudo', HOME_HELPER, operation, *args], input=json.dumps(dict(os.environ)), text=True)


def can_preserve_owner():
    if os.geteuid() == 0:
        return True
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith('CapEff:'):
            return bool(int(line.split()[1], 16) & 1)  # CAP_CHOWN
    return False


def walk_error(error):
    raise error


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def output(args):
    return run(args, stdout=subprocess.PIPE, text=True).stdout


def choose(header, values, multiple=False):
    if not values:
        return [] if multiple else None
    args = ['gum', 'choose', '--header', header,
            '--input-delimiter', '\n', '--output-delimiter', '\n',
            '--label-delimiter', '', '--strip-ansi=false']
    if multiple:
        args += ['--no-limit']
    result = subprocess.run(args, input='\n'.join(values),
                            text=True, stdout=subprocess.PIPE)
    if result.returncode:
        raise Cancelled()
    # gum prints a newline even when a multi-select is submitted with no items.
    # Split only on its configured delimiter; splitlines also splits legal
    # Unicode characters inside filenames (e.g. U+2028).
    selected = [value for value in result.stdout.split('\n') if value != '']
    if any(value not in values for value in selected):
        raise ValueError('選択結果が不正です')
    return selected if multiple else (selected[0] if selected else None)


def confirm(message):
    result = subprocess.run(['gum', 'confirm', '--default=false', message])
    if result.returncode not in (0, 1):
        raise Cancelled()
    return result.returncode == 0


def under(name, prefixes):
    return any(name == prefix or name.startswith(prefix + '/') for prefix in prefixes)


def valid_name(name):
    p = PurePosixPath(name)
    return (bool(name) and not p.is_absolute() and '..' not in p.parts
            and not any(ord(c) < 32 or ord(c) == 127 for c in name)
            and str(p) == name)


def system_allowed(name):
    return valid_name(name) and under(name, SYSTEM_ALLOWED) and not under(name, SYSTEM_PROTECTED)


def home_allowed(name):
    return valid_name(name) and not under(name, HOME_PROTECTED)


def leaves(root):
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in list(dirs):
            path = Path(parent) / name
            if path.is_symlink():
                dirs.remove(name)
                yield path
        for name in files:
            yield Path(parent) / name


def check_parents(target):
    for parent in target.parents:
        if parent.is_symlink():
            raise ValueError(f'親がシンボリックリンクです: {parent}')
        if parent.exists() and not parent.is_dir():
            raise ValueError(f'親がディレクトリではありません: {parent}')


def journal(work, event):
    with (work / 'journal.jsonl').open('a') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def install_file(target, backup, work, writer, mode=0o600, owner=None, mtime=None, metadata=None):
    """Save original before atomically replacing a leaf; never follow destination links."""
    check_parents(target)
    if target.exists() and target.is_dir() and not target.is_symlink():
        raise ValueError(f'ディレクトリとの衝突: {target}')
    existed = target.exists() or target.is_symlink()
    if existed and not (target.is_file() or target.is_symlink()):
        raise ValueError(f'既存の特殊ファイルとの衝突: {target}')
    if existed:
        check_parents(backup)
        backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if backup.exists() or backup.is_symlink():
            raise ValueError(f'退避先が存在します: {backup}')
        shutil.copy2(target, backup, follow_symlinks=False)
        if can_preserve_owner():
            old = target.lstat()
            os.chown(backup, old.st_uid, old.st_gid, follow_symlinks=False)
            shutil.copystat(target, backup, follow_symlinks=False)
    journal(work, {'status': 'pending', 'target': str(target),
                   'original': str(backup) if existed else None})
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.envbackup-', dir=target.parent)
    os.close(fd)
    tmp = Path(temporary)
    try:
        writer(tmp)
        if not tmp.is_symlink():
            os.chmod(tmp, mode & 0o777)  # Do not carry setuid/setgid bits.
        if owner is not None:
            os.chown(tmp, *owner, follow_symlinks=False)
        if mtime is not None:
            os.utime(tmp, (mtime, mtime), follow_symlinks=False)
        if metadata is not None:
            metadata(tmp)
        os.replace(tmp, target)
    finally:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
    journal(work, {'status': 'done', 'target': str(target)})


def copy_leaf(source, target, backup, work):
    info = source.lstat()
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        raise ValueError(f'通常ファイル/リンク以外です: {source}')

    def writer(tmp):
        if source.is_symlink():
            tmp.unlink()
            tmp.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, tmp)
    owner = (info.st_uid, info.st_gid) if can_preserve_owner() else None
    install_file(target, backup, work, writer, info.st_mode, owner=owner,
                 mtime=info.st_mtime,
                 metadata=lambda tmp: shutil.copystat(source, tmp, follow_symlinks=False))


def restore_home(source, home, work, protected=()):
    def allowed(path):
        name = path.relative_to(source).as_posix()
        return home_allowed(name) and not under(name, protected)
    candidates = [p for p in leaves(source) if allowed(p)]
    directories = [Path(parent) / name for parent, dirs, _ in os.walk(source, followlinks=False, onerror=walk_error)
                   for name in dirs if not (Path(parent) / name).is_symlink()
                   and allowed(Path(parent) / name)]
    candidates += directories
    groups = sorted({p.relative_to(source).parts[0] for p in candidates})
    selected = choose('戻すホーム項目（Space で選択、Enter で確定）', groups, True)
    candidates = [p for p in candidates if p.relative_to(source).parts[0] in selected]
    (work / 'home-plan.json').write_text(json.dumps(
        [p.relative_to(source).as_posix() for p in candidates], ensure_ascii=False, indent=2))
    if not candidates or not confirm(f'{home} に {len(candidates)} 項目を復元しますか？既存ファイルは退避します。'):
        return
    new_directories = []
    # Parents first, and never replace a live directory or follow directory links.
    for source_path in sorted(candidates, key=lambda p: (len(p.parts), str(p))):
        rel = source_path.relative_to(source)
        target = home / rel
        if source_path.is_dir() and not source_path.is_symlink():
            check_parents(target)
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                raise ValueError(f'ディレクトリとの衝突: {target}')
            if not target.exists():
                target.mkdir(parents=True, mode=source_path.stat().st_mode & 0o777)
                os.chmod(target, source_path.stat().st_mode & 0o777)
                new_directories.append((source_path, target))
                journal(work, {'status': 'directory-created', 'target': str(target)})
        else:
            copy_leaf(source_path, target, work / 'original-home' / rel, work)
    for source_path, target in reversed(new_directories):
        info = source_path.stat()
        if can_preserve_owner():
            os.chown(target, info.st_uid, info.st_gid)
        shutil.copystat(source_path, target, follow_symlinks=False)


def member_owner(member):
    # Numeric service IDs can differ after a reinstall. Map names, never guess.
    uid = pwd.getpwnam(member.uname).pw_uid if member.uname else member.uid
    gid = grp.getgrnam(member.gname).gr_gid if member.gname else member.gid
    if (member.uid != 0 and not member.uname) or (member.gid != 0 and not member.gname):
        raise ValueError(f'所有者名がありません: {member.name}')
    return uid, gid


def preview(target, data):
    if target.is_symlink():
        print(f'現在のリンク: {target} -> {os.readlink(target)}')
        return
    if not target.exists():
        print(f'新規: {target}')
        return
    if target.is_file() and len(data) <= 128 * 1024 and target.stat().st_size <= 128 * 1024:
        try:
            before = target.read_text().splitlines(keepends=True)
            after = data.decode('utf-8').splitlines(keepends=True)
            print(''.join(list(difflib.unified_diff(before, after,
                  fromfile=str(target), tofile='バックアップ'))[:100]))
            return
        except UnicodeError:
            pass
    print(f'バイナリまたは大きなファイル: {target}')


def restore_system(archive, work):
    if os.geteuid() != 0:
        raise ValueError('システム復元には root 権限が必要です')
    with tarfile.open(archive, 'r:*') as tar:
        members = {}
        directories = {}
        for member in tar:
            name = member.name.rstrip('/')
            if system_allowed(name) and member.isdir():
                directories[name] = member
            if system_allowed(name) and (member.isfile() or member.issym()):
                if name in members:
                    raise ValueError(f'アーカイブ内のパスが重複しています: {name}')
                members[name] = member
        selected = choose('システム復元候補（保護項目は除外済み。Space で選択）', sorted(members), True)
        (work / 'system-plan.json').write_text(json.dumps(selected, ensure_ascii=False, indent=2))
        for name in selected:
            member = members[name]
            target = Path('/') / name
            check_parents(target)
            owner = member_owner(member)
            data = None
            if member.issym():
                print(f'{target} -> {member.linkname}')
            else:
                if member.size <= 128 * 1024:
                    with tar.extractfile(member) as stream:
                        data = stream.read()
                preview(target, data if data is not None else b'\x00' * (128 * 1024 + 1))
            if not confirm(f'{target} を復元しますか？'):
                continue

            # Only initialize new parents; keep existing directory ownership/modes.
            for parent in reversed(target.parents):
                if parent.exists():
                    continue
                directory = directories.get(parent.relative_to('/').as_posix())
                directory_owner = member_owner(directory) if directory else (0, 0)
                parent.mkdir(mode=0o700)
                os.chown(parent, *directory_owner)
                os.chmod(parent, directory.mode & 0o777 if directory else 0o755)
                journal(work, {'status': 'directory-created', 'target': str(parent)})

            def writer(tmp):
                if member.issym():
                    tmp.unlink()
                    tmp.symlink_to(member.linkname)
                else:
                    with tar.extractfile(member) as src, tmp.open('wb') as dst:
                        shutil.copyfileobj(src, dst)
            install_file(target, work / 'original-system' / name, work, writer,
                         member.mode, owner, member.mtime)


def package_names(path):
    if not path.is_file():
        return []
    values = path.read_text().splitlines()
    if any(not re.fullmatch(r'[a-zA-Z0-9@_+][a-zA-Z0-9@._+\-]*', p) for p in values):
        raise ValueError(f'パッケージ一覧が不正です: {path}')
    return sorted(set(values))


def hardware_package(name):
    return (name.startswith(('linux', 'asahi', 'j715', 'j713'))
            or name in ('alsa-ucm-conf', 'alsa-card-profiles', 'uboot-asahi', 'm1n1'))


def restore_packages(lists, work):
    native = package_names(lists / 'pacman-explicit-native.txt')
    foreign = package_names(lists / 'pacman-explicit-foreign-aur.txt')
    (work / 'aur-packages.txt').write_text('\n'.join(foreign) + '\n')
    installed = set(output(['pacman', '-Qq']).splitlines())
    available = set(output(['pacman', '-Slq']).splitlines())
    missing = [p for p in native if p not in installed and not hardware_package(p)]
    (work / 'unavailable-packages.txt').write_text('\n'.join(p for p in missing if p not in available) + '\n')
    selected = choose('追加する公式パッケージ（現在のリポジトリの版）',
                      [p for p in missing if p in available], True)
    if selected and confirm(f'{len(selected)} パッケージを pacman で追加しますか？'):
        run(['sudo', 'pacman', '-S', '--needed', '--', *selected])
        journal(work, {'status': 'packages-installed', 'packages': selected})
    print('AUR・入手できないパッケージは作業フォルダの一覧から手動で確認してください。')


def snapshot_choice(snapshots, tag, host=None):
    candidates = [s for s in snapshots if tag in s.get('tags', [])
                  and (host is None or s.get('hostname') == host)]
    candidates.sort(key=lambda s: s['time'], reverse=True)
    labels = [f"{s['id']}  {s['time']}  {s.get('hostname', '?')}" for s in candidates]
    label = choose(f'復元元のスナップショット（{tag}）', labels)
    return candidates[labels.index(label)] if label else None


def wizard(args):
    if os.geteuid() == 0:
        raise ValueError('ウィザードは通常ユーザーで起動してください')
    for command in ('restic', 'gum', 'sudo', 'pacman'):
        if not shutil.which(command):
            raise ValueError(f'{command} がありません')
    restic = ['restic', '-r', args.repo]
    snapshots = json.loads(output([*restic, 'snapshots', '--json']))
    home_snap = snapshot_choice(snapshots, 'home')
    if not home_snap:
        raise ValueError('ホームのスナップショットがありません')
    system_snap = snapshot_choice(snapshots, 'system', home_snap.get('hostname'))
    paths = home_snap.get('paths', [])
    if len(paths) != 1 or not re.fullmatch(r'/home/[^/\x00-\x1f]+', paths[0]):
        raise ValueError('ホームのスナップショットは /home/<ユーザー> 1 個である必要があります')
    old_home = paths[0]
    print(f"ホーム: {home_snap['time']} / {old_home}\n復元先: {args.home}")
    print('システム: ' + (system_snap['time'] if system_snap else 'なし（ホームのみ）'))
    print('fstab・起動領域・機種固有設定・認証情報・ネットワーク情報を保護します。')
    if not confirm('選んだスナップショットを作業フォルダへ取り出しますか？'):
        return
    if not Path(HOME_HELPER).is_file():
        raise ValueError('復元用ヘルパーがありません。install.sh を再実行してください')
    root = Path(args.work_root).expanduser()
    check_parents(root / 'placeholder')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    work = Path(tempfile.mkdtemp(prefix='wizard-', dir=root))
    print(f'作業フォルダ: {work}', flush=True)
    (work / 'snapshots.json').write_text(json.dumps({'home': home_snap, 'system': system_snap}, indent=2))
    journal(work, {'status': 'started', 'home': str(args.home)})
    stage = work / 'home'
    # Restore a concrete ID, with verification, before changing the live home.
    privileged_home('restore', '-r', args.repo, home_snap['id'] + ':' + old_home,
                    '--target', stage, '--verify')
    source = stage
    if not source.is_dir() or source.is_symlink():
        raise ValueError('取り出したホームが見つかりません')
    archive = work / 'system.tar'
    if system_snap:
        with archive.open('wb') as stream:
            run([*restic, 'dump', system_snap['id'], '/system.tar'], stdout=stream)
        # Check the archive before any live changes; never extract it into /.
        with tarfile.open(archive) as tar:
            for member in tar:
                if not valid_name(member.name.rstrip('/')):
                    raise ValueError(f'不正なアーカイブパス: {member.name}')
    lists = source / '.local/state/envbackup/lists'
    if confirm('保存された一覧から追加パッケージを選びますか？'):
        restore_packages(lists, work)
    if system_snap and confirm('システム設定を差分を見ながら復元しますか？ sudo 認証が必要になる場合があります。'):
        # This is an interactive, explicitly confirmed operation, not timer sudo.
        run(['sudo', sys.executable, Path(__file__).resolve(), '--apply-system', archive, '--work', work])
    protected = []
    for path in [*args.protect_home, args.work_root, Path(__file__).resolve().parent, args.repo]:
        absolute = Path(path).expanduser()
        if absolute.is_absolute() and absolute.is_relative_to(Path(args.home)):
            relative = absolute.relative_to(Path(args.home)).as_posix()
            if relative == '.':
                raise ValueError('ホーム全体が保護対象と重なっています')
            protected.append(relative)
    apply_args = ['--apply-home', str(source), '--home', str(args.home), '--work', str(work)]
    for relative in protected:
        apply_args += ['--protect-home', relative]
    privileged_home('apply-home', *apply_args)
    journal(work, {'status': 'completed'})
    print(f'復元完了。退避ファイルと記録: {work}')
    print(f'サービスの enabled/masked 一覧などは {lists} で確認できます。')
    print('サービスの有効化・AUR の導入・ペアリングは手動で確認してください。ログアウト前に設定を確認してください。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo')
    parser.add_argument('--home', default=str(Path.home()))
    parser.add_argument('--work-root', default=str(Path.home() / 'envbackup-restore'))
    parser.add_argument('--protect-home', action='append', default=[])
    parser.add_argument('--apply-system', type=Path)
    parser.add_argument('--apply-home', type=Path)
    parser.add_argument('--work', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.apply_home:
            if not args.work or not can_preserve_owner():
                raise ValueError('ホームの適用には復元用ヘルパーと --work が必要です')
            restore_home(args.apply_home, Path(args.home), args.work, args.protect_home)
        elif args.apply_system:
            if not args.work:
                parser.error('--work is required')
            restore_system(args.apply_system, args.work)
        elif args.repo:
            wizard(args)
        else:
            parser.error('--repo is required')
    except (Cancelled, KeyboardInterrupt):
        print('復元を中止しました。取り出したデータと適用済みの変更は作業フォルダの記録を確認してください。', file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as exc:
        print(f'復元を停止しました: {exc}\n作業フォルダの journal.jsonl と退避ファイルを確認してください。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
