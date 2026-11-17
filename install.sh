#!/bin/bash
# envbackup を入れる（何度実行してもよい）。ユーザーで実行し、システムへの配置に sudo を使う。
set -euo pipefail
SRC=$(cd "$(dirname "$0")" && pwd)

command -v restic >/dev/null || sudo pacman -S --needed --noconfirm restic
for c in gum jq secret-tool python3 setpriv; do command -v $c >/dev/null || { echo "$c がありません" >&2; exit 1; }; done

mkdir -p ~/.local/bin ~/.config/envbackup ~/.config/systemd/user
ln -sf "$SRC/envbackup" ~/.local/bin/envbackup
chmod +x "$SRC/envbackup" "$SRC/envbackup-systar"
# root で実行される部分は、root の所有の場所に置く（ユーザーが書き換えられないように）
sudo install -D -m 0755 -o root -g root "$SRC/envbackup-systar" /usr/local/libexec/envbackup-systar
sudo install -D -m 0755 -o root -g root "$SRC/envbackup-read" /usr/local/libexec/envbackup-read
sudo install -D -m 0755 -o root -g root "$SRC/envbackup-restore-home" /usr/local/libexec/envbackup-restore-home
sudo install -D -m 0755 -o root -g root "$SRC/restore-wizard.py" /usr/local/libexec/envbackup-restore-wizard.py

[ -f ~/.config/envbackup/config.sh ] || cp "$SRC/config.default.sh" ~/.config/envbackup/config.sh

cp "$SRC/systemd/envbackup.service" "$SRC/systemd/envbackup.timer" ~/.config/systemd/user/
systemctl --user daemon-reload

# Omarchy のメニュー（Setup の下に Backup）
M=~/.config/omarchy/extensions/omarchy-menu.jsonc
if [ -f "$M" ] && ! grep -q '"setup.backup"' "$M"; then
  python3 -I - "$M" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
i = s.rstrip().rfind('}')
add = '''  "setup.backup": {"icon":"󰁯","label":"Backup","aliases":["backup","envbackup"],"description":"restic: USB / network"},
  "setup.backup.menu": {"icon":"󰁯","label":"Open Backup","action":"omarchy-launch-floating-terminal-with-presentation envbackup menu"},
  "setup.backup.now": {"icon":"󰁯","label":"Back Up Now","action":"omarchy-launch-floating-terminal-with-presentation envbackup backup"},
  "setup.backup.restore": {"icon":"󰦛","label":"Restore a File","action":"omarchy-launch-floating-terminal-with-presentation envbackup restore-file"},
  "setup.backup.timer": {"icon":"󰥔","label":"Daily Backup","checked":"systemctl --user -q is-enabled envbackup.timer","action":"if systemctl --user -q is-enabled envbackup.timer; then systemctl --user disable --now envbackup.timer; else systemctl --user enable --now envbackup.timer; fi"},
'''
open(p, 'w').write(s[:i] + add + s[i:])
PY
fi
echo "入れました。次: envbackup setup（パスフレーズとリポジトリ）→ envbackup backup → systemctl --user enable --now envbackup.timer"
