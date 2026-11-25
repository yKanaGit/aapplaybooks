# RHEL パッチ対応デモのセットアップ

## 1. セットアップ済みの環境で再開する

AAP の復元と Project 同期、Slack アプリの設定が済んでいれば、Mac のターミナルでリポジトリのルートへ移動し、次のコマンドを実行します。

```bash
source ~/slack-demo-venv/bin/activate
python3 --version
./run-demo.sh --serve
```

新しいターミナルを開いたら、毎回 `source ~/slack-demo-venv/bin/activate` を実行します。仮想環境を別の場所に作った場合は、その環境の `bin/activate` を指定します。起動時に表示される設定を使うときは Enter を押し、環境変数で渡していないトークンは端末に非表示で入力します。「通知 Job の監視を開始しました」と表示されたら、[デモの操作](#3-デモの操作と結果確認)に進みます。

## 2. 初回セットアップ

### Mac の仮想環境と依存ライブラリを用意する

起動スクリプトには Python 3.10 以降の仮想環境が必要です。下の例では、同じ環境で初回復元も行えるように Python 3.12 と `ansible-core` 2.20 を使います。Mac に Git と Python 3.12 を用意し、リポジトリのルートで実行します。`~/slack-demo-venv` がすでにある場合は、必要に応じて別のパスに復元用の仮想環境を作ります。

```bash
python3.12 -m venv ~/slack-demo-venv
source ~/slack-demo-venv/bin/activate
python3 --version
python3 -m pip install -r slack-bolt/requirements.txt
python3 -m pip install 'ansible-core>=2.20,<2.21'
python3 -m pip check
ansible-playbook --version
```

`slack-bolt/requirements.txt` は Slack Bolt、Slack SDK、PyYAML を導入します。`ansible-core` は `restore-demo.sh` が呼び出す `ansible-playbook` を提供します。追加の Ansible Collection は不要です。

### AAP と Slack の前提設定を済ませる

1. AAP Controller または Gateway の HTTPS ベース URL を確認します。対象 Organization、その Organization の RHEL Inventory、Inventory 内の有効な RHEL ホスト、Machine 型の SSH Credential、対象 Organization が使える Execution Environment を用意します。
2. Mac と AAP からアクセスできる公開 GitHub リポジトリに、手元と同じ `rhel_patch.yml` と `slack_notify.yml` を用意します。対象ブランチは `aap-config/current-settings.yml` の `project.scm_branch` で指定します。AAP の Project は SCM 同期でこの2本を取得します。
3. AAP の復元用と常駐サービス用に `write` スコープの OAuth トークンを用意します。復元用ユーザーには対象リソースの参照・作成・更新権限が必要です。常駐サービス用ユーザーには対象設定とジョブ成果物の参照権限、`RHEL-Patch-Slack-Workflow` の Execute 権限が必要です。AAP 管理画面から通知ジョブを起動する操作者には `RHEL-Slack-Notification` の Execute 権限を付けます。 [復元と権限の詳細](RESTORE.md)
4. Slack アプリで Interactivity と Socket Mode を有効にし、Bot Token のスコープに `chat:write`、App Token のスコープに `connections:write` を設定します。アプリを対象 Workspace にインストールし、Bot を通知先チャンネルに招待します。スコープ追加後はアプリを再インストールします。 [Slack Socket Mode](https://docs.slack.dev/tools/bolt-python/concepts/socket-mode/) · [投稿権限](https://docs.slack.dev/reference/methods/chat.postMessage/)

### 設定とトークンを渡す

設定は起動時に入力できます。環境変数を使う場合は、実行するターミナルで次のプレースホルダーを実際の値に置き換えます。許可ユーザーが複数の場合は、Slack メンバー ID をカンマで区切ります。

```bash
export AAP_URL='https://aap.example.com'
export AAP_RESTORE_OAUTH_TOKEN='<復元用 AAP OAuth トークン>'
export AAP_DEMO_OAUTH_TOKEN='<常駐サービス用 AAP OAuth トークン>'
export SLACK_TEAM_ID='<Slack Workspace ID>'
export SLACK_CHANNEL_ID='<Slack チャンネル ID>'
export SLACK_ALLOWED_USER_IDS='<許可する Slack メンバー ID>'
export SLACK_BOT_TOKEN='<Slack Bot Token>'
export SLACK_APP_TOKEN='<Slack App Token>'
```

`AAP_URL` は `run-demo.sh` の入力欄の既定値です。`restore-demo.sh` では AAP URL を入力するか、保存済みの値を採用します。復元後は `.demo-local/config.json` に `slack_team_id`、`slack_channel_id`、`slack_allowed_user_ids` を保存して再利用できます。トークンは端末で非表示入力するか、秘密ストアからプロセスの環境変数に渡します。社内 CA を使う場合は公開ルートも含む PEM バンドルを `SSL_CERT_FILE` に指定します。 [設定項目と保存方法](CONFIGURATION.md)

### 復元して起動前に確認する

AAP にデモ構成を用意する場合は、仮想環境を有効にしたまま復元の Dry Run を実行します。

```bash
./restore-demo.sh --dry-run --save-config
```

照合結果と作成・更新・再利用の予定を確認します。非秘密設定を `.demo-local/config.json` に保存する場合は `SAVE` と入力します。既存の Project を使う場合は、必要に応じて [復元手順](RESTORE.md) に沿って SCM 同期を済ませます。

```bash
./restore-demo.sh
```

予定された作成・更新と新規 Project の初回 SCM 同期を確認し、実行する場合は `RESTORE` と入力します。完了後に AAP で Project の同期結果、`RHEL-Patch-Slack-Workflow` の Check → Apply → Verify、各 RHEL テンプレートの `apply_patch: false` を確認します。

```bash
./run-demo.sh --dry-run
./run-demo.sh --serve
```

起動用 Dry Run は通知テンプレート、同期済み Playbook、Workflow の経路、対象ホスト、RHSA と `apply_patch: false` を読み取りで確認します。Slack への投稿は `--serve` で監視を開始し、次の手順で AAP の通知ジョブが成功した後に行われます。

## 3. デモの操作と結果確認

1. Mac に「通知 Job の監視を開始しました」と表示されたら、AAP 管理画面の **Templates** から `RHEL-Slack-Notification` を起動します。`slack_notify.yml` は模擬検知の CVE、RHSA、重要度、対象ホスト、検知元、イベント ID をジョブ成果物に出力します。Mac の Slack Bolt がそれを読み取り、承認／保留ボタン付きの通知を投稿します。
2. 許可ユーザーが期限内に **承認** を押すと、AAP の `RHEL-Patch-Slack-Workflow` が起動します。Check が対象を確認し、成功経路で Apply、Verify へ進みます。保留または期限切れのイベントは Workflow を起動しません。承認期限は既定で 900 秒です。
3. Slack の元通知のスレッドで Workflow と各ジョブの結果、前後の RHSA 適用候補と関連 RPM のバージョンを確認します。`./run-demo.sh --status` でもジョブ ID と記録済みの結果を確認できます。

`rhel_patch.yml` は `apply_patch` が真偽値の `false` であることを最初に検証します。Apply は `apply_patch=false | package updates skipped` と出力し、実パッチ適用をスキップします。Check と Verify は RHSA とインストール済み RPM を照会します。比較に必要な情報が不足している場合は「未確認」と表示されます。

繰り返す場合は Mac のサービスを起動したまま、AAP で新しい `RHEL-Slack-Notification` ジョブを実行します。Ctrl-C で Mac の待ち受けを停止できます。停止中に起動して未検出だった通知ジョブの代わりに、監視を再開してから新しい通知ジョブを起動します。 [繰り返し実行と状態確認](RESTORE.md#3-mac-のサービスを起動しaap-管理画面から繰り返す)

## 4. よくあるエラー

**「Python 3.10 以降の仮想環境を有効にしてください」** と表示されたら、起動コマンドを実行するターミナルで仮想環境を有効にし、バージョンを確認します。

```bash
source ~/slack-demo-venv/bin/activate
python3 --version
```

仮想環境がまだなければ、Python 3.10 以降が使えることを確認してから作成します。以下はデモ起動用の例です。AAP の復元も行う場合は、[初回セットアップ](#2-初回セットアップ)の Python 3.12 と `ansible-core` の手順を使います。

```bash
python3 --version
python3 -m venv ~/slack-demo-venv
source ~/slack-demo-venv/bin/activate
python3 -m pip install -r slack-bolt/requirements.txt
python3 -m pip check
```

仮想環境を有効にしても Python が 3.10 未満の場合は、対応する Python で別の仮想環境を作成します。依存関係の不足も、実際に起動する仮想環境で上記の `pip install` と `pip check` を実行して確認できます。 [トラブルシューティング](TROUBLESHOOTING.md)

## 5. AAP 環境を作り直した場合

同じ AAP 環境で Mac の待ち受けを再起動するだけなら、[再開手順](#1-セットアップ済みの環境で再開する)で `run-demo.sh --serve` を実行します。新しい OpenTLC サンドボックスなどで AAP 環境を作り直した場合は、新しい AAP URL と OAuth トークンを用意し、Inventory や Credential を確認してから `restore-demo.sh` でデモ構成を復元します。Project の同期と `run-demo.sh --dry-run` の確認後に待ち受けを起動します。 [新しい AAP 環境への復元手順](RESTORE.md)
