# Slack Bolt と AAP Workflow 承認

Controller 4.5.x の `/api/v2/` または AAP 2.5／2.6 の Gateway 経由で、すでに承認待ちになっている `RHEL-Patch-Workflow` の承認ノードを Slack から操作します。対象は 1 回の実行につき 1 件です。

従来の `RHEL-Slack-Notification` は独立した通知です。AAP 連携用の通知は `--serve` が Slack に別途投稿します。その通知には Workflow Job ID と Approval ID が表示されます。アプリは Workspace、チャンネル、メッセージの時刻 ID、ボタンの識別値、および許可ユーザーを照合します。

## セットアップ

Mac の zsh と Python 3.10 以降を使用します。リポジトリーのルートで、Bolt を実行する仮想環境を有効にしてから依存関係をインストールします。依存ファイルには `import yaml` に必要な PyYAML が含まれます。復元時に作成した仮想環境も使用できます。

```zsh
python3 -m venv "$HOME/.venvs/aap-slack-bolt"
source "$HOME/.venvs/aap-slack-bolt/bin/activate"
python3 -m pip install -r slack-bolt/requirements.txt
python3 -m pip check
```

Slack アプリで Socket Mode と Interactivity を有効にします。App Token には `connections:write`、Bot Token には `chat:write` を付け、Bot を通知先チャンネルに招待します。Scope を追加した場合は Slack アプリを Workspace に再インストールします。 [Socket Mode](https://docs.slack.dev/tools/bolt-python/concepts/socket-mode/) · [chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/)

AAP にはこの連携専用のユーザーを用意し、権限を対象リソースに絞ります。

| 対象 | 権限 |
| --- | --- |
| `RHEL-Patch-Workflow` | Approve |
| `RHEL-Patch-Check`、`RHEL-Patch-Apply` | Read。実行された Check Job と Apply Template の安全確認に使用 |
| Controller OAuth トークン | 承認または拒否を送信する場合は `write` スコープ |

Workflow の起動には、別途起動権限を持つ人のアカウントを使用します。専用ユーザーのロールは対象 Workflow の Approve と上記 Job Template の Read に絞ります。 [Workflow の承認権限](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.4/html/automation_controller_user_guide/controller-workflow-job-templates) · [OAuth スコープ](https://docs.redhat.com/en-us/documentation/red_hat_ansible_automation_platform/2.4/pdf/automation_controller_administration_guide/Red_Hat_Ansible_Automation_Platform-2.4-Automation_Controller_Administration_Guide-en-US.pdf)

## ダミー CVE から統合実行

`./run-demo.sh --serve` は AAP の `RHEL-Slack-Notification` ジョブを継続監視します。ジョブ成果物から Slack に通知を送り、許可ユーザーの承認を受けてから `RHEL-Patch-Slack-Workflow` を起動します。 [統合デモの手順](../RESTORE.md)

## 手動起動済み Workflow を自動検索

AAP 上で Workflow を承認待ちまで進め、リポジトリーのルートで `./start-slack-demo.sh --dry-run` を実行すると候補を読み取れます。`./start-slack-demo.sh` は候補を選択し、`START` を入力した後で待ち受けを開始します。非秘密設定を保存するときは `--save-config` を指定します。 [復元と運用手順](../RESTORE.md)

## ID を指定して連携

1. AAP で両パッチテンプレートの `apply_patch: false` と、Check → Approval → Apply の成功経路を確認します。起動の承認を得た後、AAP で Workflow を手動で起動し、Approval 待ちまで進めます。
2. AAP の Jobs 画面で **Workflow Job ID** を確認し、**Views → Workflow Approvals** または実行中の Workflow の承認ノードから **Approval ID** を確認します。Approval ID は実行中の承認インスタンスの ID です。
3. 対象の Workspace、通知先チャンネル、承認を許可する Slack ユーザー ID を設定します。複数ユーザーはカンマ区切りです。秘密値はすでに設定済みなら再入力不要です。

```zsh
export AAP_URL='https://controller.example.com'
export SLACK_TEAM_ID='T0123456789'
export SLACK_CHANNEL_ID='C0123456789'
export SLACK_ALLOWED_USER_IDS='U0123456789'
read -rs 'AAP_OAUTH_TOKEN?AAP OAuth token: '
print
export AAP_OAUTH_TOKEN
read -rs 'SLACK_BOT_TOKEN?Slack Bot token: '
print
export SLACK_BOT_TOKEN
read -rs 'SLACK_APP_TOKEN?Slack App token: '
print
export SLACK_APP_TOKEN
```

4. 下記の ID を実際の値に置き換えます。対象と設定が一致することを確認し、実地検証の承認を得てから `--serve` を実行します。既存の Bolt プロセスが同じ App Token を使っている場合は先に停止してください。

```zsh
python slack-bolt/slack_app.py --check --workflow-job-id 123 --approval-id 456
python slack-bolt/slack_app.py --diagnose --workflow-job-id 123 --approval-id 456
python slack-bolt/slack_app.py --serve --workflow-job-id 123 --approval-id 456
```

| ボタン | 動作 |
| --- | --- |
| 承認 | 許可ユーザーのクリック後に承認待ち・期限・実行グラフ・安全な変数を確認し、対象の `POST /api/v2/workflow_approvals/{id}/approve/` を実行 |
| 保留 | 既定の `AAP_HOLD_ACTION=hold` では当該通知の受付を終了し、AAP の承認は保留のまま |
| 保留を拒否として扱う | `AAP_HOLD_ACTION=deny` を設定した場合、失敗・常時経路がないことを確認して `POST /api/v2/workflow_approvals/{id}/deny/` を実行 |

承認が成功すると、実行中の Workflow は AAP 上で Apply ノードへ進みます。連携は `RHEL-Patch-Apply` の `apply_patch: false`、Workflow と Apply ノードの変数上書きの有無、Check Job の Artifacts を確認します。対象の Project はレビュー済みのデモ Playbook を使い、承認を待つ間は構成を固定してください。 [Workflow の変数と Artifacts](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.4/html/automation_controller_user_guide/controller-workflows)

## 起動に失敗したとき

| モード | 確認内容 |
| --- | --- |
| `--check` | AAP への GET で Controller のバージョン、指定した承認、承認権限とデモ設定を確認 |
| `--diagnose` | AAP の確認に加え、Slack の Bot Token と Workspace、取得可能な scope・チャンネル情報・ユーザー情報、App Token による Socket 接続先発行、通知記録を確認。Slack 投稿や WebSocket の開始は行わない |
| `--serve` | AAP バージョンと Slack 設定を確認し、新規通知の前に AAP の承認待ちを確認。通知記録を保存または再利用してから WebSocket を開始 |

診断には、実際に `--serve` を起動する仮想環境と環境変数を使用します。出力には失敗した段階、例外名、確認できる Slack API エラーコードまたは AAP の HTTP ステータスが表示されます。

- `ModuleNotFoundError` が出た場合は、Bolt を実行する仮想環境を有効にして `python3 -m pip install -r slack-bolt/requirements.txt` を実行します。`No module named 'yaml'` は、その Python 環境に PyYAML が入っていないときに表示されます。
- Workspace が不一致の場合は、Bot をインストールした Workspace の ID と `SLACK_TEAM_ID` を照合します。`missing_scope` が投稿時に出た場合は Bot の `chat:write` を確認します。
- `not_in_channel` の場合はチャンネル ID と Bot の参加状態を確認し、Bot を招待します。チャンネルとユーザーを API で照合できるのは、Bot に対応する読み取り権限がある場合です。読み取り権限がない場合は Slack 画面で確認します。許可リストはユーザー名ではなく Slack ユーザー ID を指定します。 [auth.test](https://docs.slack.dev/reference/methods/auth.test/) · [conversations.info](https://docs.slack.dev/reference/methods/conversations.info/)
- `[Slack Socket Mode 開始]` または `[Slack Socket Mode 事前確認]` のエラーは、App Token と `connections:write`、Socket Mode の有効化を確認します。 [apps.connections.open](https://docs.slack.dev/reference/methods/apps.connections.open/)

## 重複処理と停止

- 通知の対応と処理状態は、既定で `~/.local/state/aap-slack-approval/bindings.sqlite3` に記録します。ファイルにはトークンを保存しません。`AAP_SLACK_STATE_PATH` で変更できます。同じ Controller と Approval ID について、再起動後も同じ状態ファイルを使ってください。
- Socket Mode の開始に失敗しても、保存済みの通知を再利用します。Slack が投稿を明示的に拒否した場合は、設定を直して再試行できます。投稿結果が未確定の場合は再投稿を止めます。旧版の失敗で通知記録が保存されなかった可能性がある場合は、再試行前に Slack で Workflow Job ID と Approval ID の組み合わせを確認してください。
- 最初に処理権を確保したクリックだけが書き込みへ進みます。Controller 側で期限切れや処理済みになった場合は、AAP で現在の状態を確認してください。POST の結果が通信障害などで不明な場合も、再送は止めて AAP で結果を確認します。
- デモ終了は Ctrl-C で Socket Mode を停止します。既定の保留の後、Workflow 自体を終了する判断は AAP 上で行います。

## ローカル検証

```zsh
python -B -m unittest discover -s slack-bolt -v
python -B slack-bolt/slack_app.py --help
```
