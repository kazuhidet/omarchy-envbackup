# envbackup

restic を使って、Omarchy の環境を USB とネットワークの両方にバックアップします。バックアップの対象は、ホーム、システムの設定、パッケージの一覧です。Omarchy のメニューの **Setup → Backup** から開けます。

## 入れ方
```
./install.sh                 # restic、envbackup、root 用の補助スクリプト、タイマー、メニュー
envbackup setup              # パスフレーズを GNOME キーリングへ。リポジトリを作る
envbackup backup             # 1 回目
systemctl --user enable --now envbackup.timer   # 毎日 12:30（逃した分は次のログイン時）
```

## sudo / NOPASSWD

`kazu` 向けの設定を `sudoers/` に用意しています。配置先は `/etc/sudoers.d/` です。

| 操作 | sudo で実行するもの |
|---|---|
| バックアップ前の権限確認 | `true`（引数なし） |
| ホーム・pacman キャッシュの読み取り | `/usr/local/libexec/envbackup-read`（起動後に通常ユーザーへ戻り、読み取り権限のみ追加） |
| パッケージのファイル検査 | `pacman -Qkk` |
| パッケージに属さないファイルの調査 | `find /etc /usr /opt -xdev ( -type f -o -type l )` |
| システムのアーカイブ作成 | `/usr/local/libexec/envbackup-systar` と設定済みの対象パス |
| システムファイルの復元 | `tar`、`test`、`diff`、`mv`、`mkdir`、`cp` |
| インストール時のみ | `pacman -S --needed --noconfirm restic`、補助スクリプトの `install` |

自動バックアップ用（引数を固定）:

```bash
visudo -cf sudoers/envbackup-backup
sudo install -D -o root -g root -m 0755 envbackup-systar /usr/local/libexec/envbackup-systar
sudo install -D -o root -g root -m 0755 envbackup-read /usr/local/libexec/envbackup-read
sudo install -o root -g root -m 0440 sudoers/envbackup-backup /etc/sudoers.d/envbackup-backup
sudo visudo -c
sudo -n true
sudo -n /usr/local/libexec/envbackup-read --check
```

補助スクリプトと親ディレクトリは root 所有で、一般ユーザーが書き換えられないことが前提です。
`envbackup-read` は `setpriv` で実行ユーザー・グループへ戻り、`CAP_DAC_READ_SEARCH` だけを保持して restic を実行します。ホーム内の root / 他ユーザー所有ファイルや pacman のダウンロード途中のディレクトリも、所有権・パーミッションを変更せず保存します。この許可は他ユーザーのファイルを読み取れる権限を含みます。キーリング・SSH・ネットワークの認証環境はユーザーのものを引き継ぎ、リポジトリやキャッシュはユーザー所有で作成します。restic バイナリへの永続的な `setcap` は不要です。
`SYSTEM_PATHS` を変更したら、`envbackup-backup` の補助スクリプトの引数も同じ順序で変更し、再検証・再配置してください。別ユーザーでは両ファイルの `kazu` を変更します。

システムファイルの復元もパスワードなしにする場合は、追加で次を配置します。
**この設定は任意の場所への root 権限の書き込みを許すため、実質的に root の全権限を与えます。** 復元用コマンドは可変のパスを受け取るため、引数を制限していません。

```bash
visudo -cf sudoers/envbackup-restore
sudo install -D -o root -g root -m 0755 envbackup-read /usr/local/libexec/envbackup-read
sudo install -D -o root -g root -m 0755 envbackup-restore-home /usr/local/libexec/envbackup-restore-home
sudo install -D -o root -g root -m 0755 restore-wizard.py /usr/local/libexec/envbackup-restore-wizard.py
sudo install -o root -g root -m 0440 sudoers/envbackup-restore /etc/sudoers.d/envbackup-restore
sudo visudo -c
```

インストール操作は NOPASSWD の対象に含めていません。状態表示、マウント、`systemctl --user` のタイマー操作に sudo は不要です。全体復元のホーム取り出し・検証・書き戻しは `envbackup-restore-home` を sudo 経由で使用します。バックアップ用ポリシーだけでは復元権限を付与しません。

## 保存するもの
| タグ | 中身 |
|---|---|
| `home` | `/home/kazu`。`.cache`、ゴミ箱、Chromium のキャッシュは除く。`~/.local/state/envbackup/lists/`（パッケージの版、変更された設定ファイル、有効なサービス、パッケージに属さないファイル、SSH のホスト鍵の指紋など）を含む |
| `system` | root の tar を 1 つのファイル `system.tar` にしたもの。`/etc`、`/root`、`/opt`、`/usr/local`、`/var/lib` の一部 |
| `pkgcache` | `/var/cache/pacman/pkg`（`PKGCACHE=1` のとき） |

- 2 回目からは差分だけを保存します。
- 古いスナップショットは、日ごと 7 個、週ごと 4 個、月ごと 6 個を残して整理します。不要になったデータの削除（prune）は日曜日だけ行います。
- 読めないファイルがあった場合（restic 終了コード3）は不完全なバックアップとして失敗を通知します。他のタグの保存は続行しますが、そのリポジトリの世代整理は行いません。

### リポジトリがロックされている場合

各 restic 処理はロック解除を最大30秒待ちます。以前の異常終了でロックが残ったときは、他のバックアップ処理が動いていないことを確認して `envbackup unlock` を実行します。リポジトリを選んで確認後、restic の通常の `unlock` で古いロックだけを解除します。実行中のロックを強制解除する `--remove-all` は使用しません。

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
- **全体を復元する**：`envbackup restore` で新規インストール後の復元ウィザードを開きます（メニューにもあります）。
- **ファイルを取り出す**：スナップショットとファイルを選ぶと、`~/envbackup-restore/<日時>/` に取り出します。そのあと差分を見せて、確認してから元の場所に戻します。今のファイルは `.envbackup-<日時>` を付けて残します。
- **フォルダとして開く**：`restic mount` で `~/envbackup-mount` に開きます。

### 全体復元の流れ

1. `envbackup setup` で元のパスフレーズをキーリングに登録し、`envbackup restore` を起動します。
2. 全ホストからホームのスナップショットを選び、同じホストのシステムのスナップショットを選びます。日時はそれぞれ表示されるので、同じバックアップ実行のものを選んでください。現在のユーザー名・ホスト名が異なっていても復元できます。
3. ホームを検証付きで作業フォルダへ取り出し、system.tar を取得します。この時点では実環境へ書き戻しません。
4. 保存済みの一覧から不足する公式パッケージを選択します。現在のリポジトリの版を導入し、カーネル・Asahi/J715/J713 関連は除きます。AUR と入手できないパッケージは一覧に残します。
5. システムのファイルを選び、テキストの差分（先頭100行まで）を見て1件ずつ復元します。バイナリ・大きいファイル・リンクも個別に確認します。
6. ホームのトップレベル項目を選んで現在のホームへ復元します。既存ファイルの削除や、既存ディレクトリの丸ごと置換はしません。ディレクトリとファイルの衝突やリンク経由の書き込みはエラーで停止します。

ホームの取り出しと `--verify`、候補の走査、退避、書き戻しは復元用ヘルパーの権限で行います。root や他ユーザー所有、権限 `000` の項目も対象です。書き戻すファイル・リンクと新規ディレクトリは、保存時の数値 UID/GID・モード・更新時刻・拡張属性（ACL を含む）を引き継ぎます。既存ディレクトリの属性は変更しません。別マシンで数値 UID/GID が異なる場合は所有者の対応を確認してください。読み取れないディレクトリは黙って除外せず、エラーで停止します。

システム変更とパッケージ導入には通常の対話式 sudo を使います。ホームの全体復元にも所有者・アクセス権を復元する権限が必要です。復元用ヘルパーは実行ユーザーを維持したまま DAC 読み書き、所有者変更、属性復元の capabilities を使います。NOPASSWD にする場合は更新した `sudoers/envbackup-restore` を配置します。この権限は任意の場所へ書き込めるため、実質的な root 権限です。

`/etc/fstab`、machine-id、インストール完了マーカー、ホスト情報、ユーザー・グループ、sudo・PAM・SSH・証明書、ネットワーク、pacman、起動・音声・機種固有設定、systemd のユニットを保護します。システムの候補は `/etc`、`/opt`、`/usr/local`、`/root` の通常ファイルとシンボリックリンクのみです。`/boot`、ESP、カーネル、`/usr/share`、`/var/lib` のペアリング・指紋・J715 状態は復元しません。ハードリンクと特殊ファイルも対象外です。

ホームでは新しいキーリング、envbackup の設定・状態、ユーザーの systemd 設定、作業フォルダ、実行中のツールを保護します。ホーム内のローカルリポジトリも保護します。`.ssh` などの秘密情報は選択したホーム項目に含まれるので、必要なものを選んでください。

上書き前のファイルは `~/envbackup-restore/wizard-*/original-home/` と `original-system/` に保存します。`journal.jsonl` に適用記録、`home-plan.json` と `system-plan.json` に選択内容が残ります。エラー・中断時はそこで停止します。自動ロールバックはありません。退避ファイルから必要なものを元のパスへコピーしてください（システムは sudo が必要）。新規作成項目には退避ファイルがありません。

システムの所有者はユーザー・グループ名で現在の ID に対応付け、存在しなければ停止します。通常のモード・所有者・更新時刻を復元しますが、setuid/setgid、ACL、システムの拡張属性は再適用しません。サービスの enabled/masked 一覧は取り出したホームの `.local/state/envbackup/lists/` に残します。サービス有効化、AUR、機器のペアリング、特別な権限設定は確認して手動で行ってください。自動再起動はしません。

## これから
- J713 用の設定。
