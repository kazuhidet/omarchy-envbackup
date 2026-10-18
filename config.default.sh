# envbackup の設定（~/.config/envbackup/config.sh にコピーして使う）
# bash として読み込まれる。

# USB: このラベルのボリュームの中に restic のリポジトリを作る。挿していなければ飛ばす。
USB_LABEL=Transfer
USB_DIR=restic-omarchy

# ネットワーク: restic のリポジトリ URL。空なら使わない。例:
#   NET_REPO="sftp:kazu@nas.example:/srv/restic/omarchy"
#   NET_REPO="rest:https://user:pass@backup.example:8000/omarchy"
NET_REPO=""

# ホームから除外するもの（restic の --exclude パターン）
HOME_EXCLUDES=(
  "$HOME/.cache"
  "$HOME/.local/share/Trash"
  "$HOME/envbackup-restore"
  "$HOME/envbackup-mount"
  "$HOME/.config/chromium/*/Cache"
  "$HOME/.config/chromium/*/Code Cache"
  "$HOME/.config/chromium/*/GPUCache"
  "$HOME/.config/chromium/*/DawnGraphiteCache"
  "$HOME/.config/chromium/*/DawnWebGPUCache"
  "$HOME/.config/chromium/*/Service Worker/CacheStorage"
  "*.sock"
)

# システム: / からの相対パス。root の tar でまとめて 1 つのファイル（system.tar）として保存する。
SYSTEM_PATHS=(
  etc root opt usr/local
  var/lib/j715 var/lib/bluetooth var/lib/NetworkManager var/lib/iwd var/lib/fprint
)

# pacman のキャッシュ（/var/cache/pacman/pkg）も保存する（1/0）。版を合わせて戻すときに使う。
PKGCACHE=1

# 古いスナップショットの整理
KEEP=(--keep-daily 7 --keep-weekly 4 --keep-monthly 6)
# 不要になったデータの削除（prune）は重いので、この曜日だけ行う（1=月 … 7=日）
PRUNE_WEEKDAY=7
