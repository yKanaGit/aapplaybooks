# 設定と秘密情報の管理

ファイルに保存するのは非秘密設定だけです。`aap-config/demo.local.example.json` は Git で管理する設定例で、Git 管理外の `.demo-local/config.json` のひな型でもあります。必要ならプレースホルダーのままコピーできます。

```zsh
mkdir -p .demo-local
cp -n aap-config/demo.local.example.json .demo-local/config.json
```

実際の値を記録するには、`restore-demo.sh` または `start-slack-demo.sh` に `--save-config` を付け、保存予定の項目を確認して `SAVE` と入力します。ファイルは 0600 で保存されます。復元ツールは保存済みの値を既定値として提示し、空欄なら同梱の既定値を使います。Slack 起動ツールでは、該当する環境変数、ローカル設定、同梱の既定値の順で既定値を選びます。

| 項目 | 取得場所 | 設定先・保存方法 | 新しい環境での扱い |
| --- | --- | --- | --- |
| AAP URL | OpenTLC の接続情報、AAP のブラウザー URL | `aap_url`。Slack 起動時は `AAP_URL` も使用可 | 更新 |
| Organization | AAP の Organizations | `organization` | 確認、変更時に更新 |
| RHEL Inventory | AAP の Inventories | `inventory` | 確認、変更時に更新 |
| 対象ホスト | Inventory の Hosts | `host` | 確認、変更時に更新 |
| SSH Credential | AAP の Credentials で Machine 型 | `ssh_credential` に名前のみ。秘密鍵は AAP に保持 | 確認、変更時に更新 |
| Execution Environment | AAP の Execution Environments | `execution_environment` | 確認、変更時に更新 |
| GitHub URL・ブランチ | GitHub のリポジトリーと対象ブランチ | `github_url` と現行 `current-settings.yml` の `project.scm_branch` | 通常は再利用 |
| Slack Workspace | Slack 管理画面、アプリのインストール先 | `slack_team_id` または `SLACK_TEAM_ID` | Workspace が変わる場合に更新 |
| Slack Channel | チャンネル詳細の ID | `slack_channel_id` または `SLACK_CHANNEL_ID` | チャンネルが変わる場合に更新 |
| Slack 許可ユーザー | Slack プロフィールのメンバー ID | `slack_allowed_user_ids` または `SLACK_ALLOWED_USER_IDS`。カンマ区切り | 運用担当者が変わる場合に更新 |
| 保留の動作 | デモ運用方針 | `hold_action` または `AAP_HOLD_ACTION`。既定の `hold`、または `deny` | 通常は再利用 |
| 復元用 OAuth | 対象 Controller または Gateway の Tokens | 非表示入力、または `AAP_RESTORE_OAUTH_TOKEN` | 新環境で発行 |
| 統合デモ用 AAP OAuth | 統合 Workflow の Execute と対象設定の参照権限がある専用ユーザーの Tokens | 非表示入力、または `AAP_DEMO_OAUTH_TOKEN` | 新環境で発行 |
| Slack 承認用 AAP OAuth | 対象環境で連携専用ユーザーの Tokens | 非表示入力、または `AAP_OAUTH_TOKEN` | 新環境で発行 |
| Slack Bot Token と App Token | Slack App の OAuth & Permissions、Basic Information | 非表示入力、または `SLACK_BOT_TOKEN` と `SLACK_APP_TOKEN` | アプリ変更・失効時に更新 |
| 統合デモのイベント ID・期限 | Mac のランナーが生成。期限は既定 900 秒 | 非秘密のイベント状態ファイル | イベントごとに変わる |
| Workflow Job ID と Approval ID（手動デモ） | 実行中 Workflow の API から検索 | 起動ごとに選択。固定設定には保存しない | 実行ごとに変わる |

長期保存が必要な秘密は macOS キーチェーンなどの秘密ストアに保存し、起動時に非表示入力またはプロセス環境へ渡します。復元用・統合デモ用・Slack 承認専用の OAuth は分けます。既定の通知状態 DB `~/.local/state/aap-slack-approval/bindings.sqlite3` は再投稿を防ぐために実行単位の ID と処理状態を記録します。統合デモは同じディレクトリーのイベント状態ファイルに通知情報、承認者、期限、起動結果を記録します。トークンは記録しません。

## Push 前の確認

`git status --short` と `git diff --cached --name-only` で対象ファイルを確認します。`git check-ignore .demo-local/config.json` でローカル設定の除外を確認します。非秘密設定の例と文書は管理できますが、実環境の設定、トークン、Webhook URL、秘密鍵、状態 DB はコミット対象から外してください。差分の内容確認は手元で行い、秘密値をターミナル共有やチャットへ貼り付けないでください。
