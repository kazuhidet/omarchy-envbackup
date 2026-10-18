# envbackup

restic を使って、Omarchy の環境を USB とネットワークの両方にバックアップします。バックアップの対象は、ホーム、システムの設定、パッケージの一覧です。Omarchy のメニューの **Setup → Backup** から開けます。

## 入れ方
```
./install.sh                 # restic、envbackup、root 用の補助スクリプト、タイマー、メニュー
envbackup setup              # パスフレーズを GNOME キーリングへ。リポジトリを作る
envbackup backup             # 1 回目
systemctl --user enable --now envbackup.timer   # 毎日 12:30（逃した分は次のログイン時）
```

## 保存するもの
| タグ | 中身 |
|---|---|
| `home` | `/home/kazu`。`.cache`、ゴミ箱、Chromium のキャッシュは除く。`~/.local/state/envbackup/lists/`（パッケージの版、変更された設定ファイル、有効なサービス、パッケージに属さないファイル、SSH のホスト鍵の指紋など）を含む |
| `system` | root の tar を 1 つのファイル `system.tar` にしたもの。`/etc`、`/root`、`/opt`、`/usr/local`、`/var/lib` の一部 |
| `pkgcache` | `/var/cache/pacman/pkg`（`PKGCACHE=1` のとき） |

- 2 回目からは差分だけを保存します。
- 古いスナップショットは、日ごと 7 個、週ごと 4 個、月ごと 6 個を残して整理します。不要になったデータの削除（prune）は日曜日だけ行います。

## 設定
`~/.config/envbackup/config.sh`
- USB：ラベル `Transfer` のボリュームの中の `restic-omarchy/` を使います。挿してあってマウントされていなければ、udisks でマウントします。
- ネットワーク：`NET_REPO` に restic のリポジトリ URL を書きます（`sftp:` や `rest:` など）。

どちらも、使えないときは飛ばして、結果を通知で知らせます。

## 前提
- `sudo -n` が使えること。システムの tar を root で取るためです。
- `/usr/local/libexec/envbackup-systar` は root の所有で置きます。ユーザーが書き換えられないようにするためです。
- パスフレーズは GNOME キーリングに置きます。そのため、タイマーはログイン中にだけ動きます。
- **パスフレーズは、キーリングとは別にも控えておいてください。** 新規インストールのあとはキーリングが空なので、控えがないと復元できません。

## 取り出し
- **ファイルを取り出す**：スナップショットとファイルを選ぶと、`~/envbackup-restore/<日時>/` に取り出します。そのあと差分を見せて、確認してから元の場所に戻します。今のファイルは `.envbackup-<日時>` を付けて残します。
- **フォルダとして開く**：`restic mount` で `~/envbackup-mount` に開きます。

## これから
- 新規インストール後の全体の復元（ウィザード）。2026-10-18 の手作業での判断（戻さないもの、差分を見て選ぶもの、特別に扱うもの）を、ルールとして組み込みます。
- J713 用の設定。
