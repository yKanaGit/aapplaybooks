# 新しい OpenTLC 環境への復元

## 1. 新環境を確認

1. OpenTLC でサンドボックスを開き、AAP Controller または Gateway の HTTPS ベース URL を確認します。
2. AAP に対象 Organization、通常の Inventory と有効な RHEL ホスト、Machine 型の SSH Credential、Execution Environment があることを確認します。復元はこれらを再利用します。
3. 復元用 OAuth トークンを発行します。本実行は対象リソースの作成権限と `write` scope が必要です。
4. 公開 GitHub の指定ブランチに現行 `rhel_patch.yml` と `slack_notify.yml` があることを確認します。ブランチは `aap-config/current-settings.yml` の `project.scm_branch` です。統合デモには更新コマンドを取り除いた現行の `rhel_patch.yml` が必要です。既存の Project を再利用するときは、復元作業の一環としてこの版へ SCM 同期してください。

## 2. Dry Run と復元

[セットアップ](SETUP.md) で復元用に準備した仮想環境を有効にし、Dry Run を実行します。ローカル設定があれば、引き継げる項目は Enter で採用できます。新しいサンドボックスの URL は入力し直してください。

```zsh
./restore-demo.sh --dry-run --save-config
```

Dry Run は AAP と GitHub を GET で照合します。非秘密設定を保存する場合は、表示内容を確認して `SAVE` と入力します。`--save-config` を付けなければファイルを書きません。通知ジョブは AAP の成果物だけを出力するため、Incoming Webhook URL の入力は不要です。

再利用対象、新規作成対象、`RHEL-Slack-Notification` の Extra Variables の更新予定を確認した後、本実行します。表示された作成・更新と、該当する初回 Project 同期を承認する場合に `RESTORE` と入力します。

```zsh
./restore-demo.sh
```

`RHEL-Slack-Notification` は Extra Variables 全体を Git の復元定義に合わせます。定義にない変数は削除されます。既存 Credential と手動承認用 Workflow は再利用します。既存の統合 Workflow が定義どおりの Check → Apply なら、Dry Run に Verify 追加が表示されます。本実行で承認すると、必要に応じて `RHEL-Patch-Verify` を作成し、Apply の成功経路に接続します。想定外の既存設定に競合があれば停止します。既存環境の移行は、Mac の常駐サービスを止め、実行中 Workflow が終了してから行います。完了後に Project の同期結果と Revision、統合 Workflow の Check → Apply → Verify、各 RHEL テンプレートの `apply_patch: false` を確認します。

## 3. Mac のサービスを起動し、AAP 管理画面から繰り返す

Mac サービス用の AAP ユーザーには、`RHEL-Slack-Notification` とそのジョブ成果物、検証対象の Project、Inventory、Organization、Check／Apply／Verify Job Template とそれぞれのジョブ成果物 と Workflow の参照権限、および `RHEL-Patch-Slack-Workflow` の Execute 権限を付けます。Mac のサービスでは `write` scope の OAuth トークンを使います。AAP 管理画面の操作者には `RHEL-Slack-Notification` の Execute 権限が必要です。Slack の許可ユーザーとトークンは [セットアップ](SETUP.md) の手順で設定してください。 [AAP 2.4 の Workflow 権限](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.4/html/automation_controller_user_guide/controller-workflow-job-templates)

```zsh
./run-demo.sh --dry-run
./run-demo.sh --serve
```

1. Dry Run で通知 Template と同期済みの通知 Playbook、Workflow の成功経路、対象ホストと RHSA、Check／Apply／Verify の `apply_patch: false` を確認します。Slack への投稿と AAP への書き込みは発生しません。
2. 「通知 Job の監視を開始しました」と表示されたら、AAP 管理画面の Templates で `RHEL-Slack-Notification` の起動ボタンを押します。通知 Job が成功すると、成果物から CVE、RHSA、重要度、対象ホスト、検知元、イベント ID を読み取り、Slack Bolt が承認／保留ボタン付き通知を投稿します。検知元の模擬データは `aap-config/current-settings.yml` の `target.source` で管理します。
3. 許可ユーザーが期限内に承認すると、対象ホストと RHSA を渡して `RHEL-Patch-Slack-Workflow` が起動します。Check でホストと RHSA を再確認し、成功した場合だけ Apply へ進みます。Check は適用前、Verify は Apply 成功後の RHSA 適用候補と関連 RPM のインストール済みバージョンを成果物に記録します。Apply はパッケージ更新をスキップします。Slack の元通知スレッドで Workflow と各ステップの結果、前後の状態、関連 RPM の変更有無を確認できます。情報不足や比較範囲の違いで判定できない場合は「未確認」と表示します。ジョブが成功しても脆弱性の解消は未確認として扱います。 [Red Hat のセキュリティアドバイザリー参照手順](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/8/html-single/managing_and_monitoring_security_updates/index)
4. Mac サービスを起動したまま手順 2 に戻ります。各通知 Job に別のイベント ID と承認期限が割り当てられます。Workflow が実行中の場合は、その完了後に別イベントを期限内に承認します。保留または期限切れのイベントは Workflow を起動しません。

`./run-demo.sh --status` で通知 Job ID、イベント ID、Slack 操作者、Workflow Job ID、実行結果と結果通知状態を確認できます。承認期限は既定で 900 秒です。起動時に `--approval-wait 秒数` で調整できます。

サービスは起動時点より新しい通知 Job を検出します。停止中に実行して未検出だったジョブを通知したい場合は、サービス再開後に新しい通知 Job を実行してください。すでに記録済みの承認待ちや起動済み Workflow は、同じローカル状態パスと設定で再開できます。Slack 投稿や AAP 起動の結果が未確定の場合は、自動再送を防ぐため状態ファイルを保持し、`--status` と該当サービスの履歴を確認してください。起動結果が未確定の記録がある間は、同じサービスからの次の Workflow 起動も保留します。

Ctrl-C で Mac の待ち受けを停止できます。起動済みの Workflow は AAP 上で実行を継続します。従来の `RHEL-Patch-Workflow` と `start-slack-demo.sh` は AAP の Approval Node を使う手動デモ用です。
