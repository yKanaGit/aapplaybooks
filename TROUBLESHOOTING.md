# トラブルシューティング

| 症状 | 確認と対処 |
| --- | --- |
| Gateway 側の ping が HTTP 404 | AAP 2.4／Controller 4.5.x は従来型 `/api/v2/` を使います。ツールが Gateway の 404 後に切り替えることと、URL がベース URL であることを確認します。 |
| AAP API が HTTP 401 または 403 | URL と同じ環境で発行したトークンか、有効期限、scope、対象リソースの権限を確認します。復元用と承認用の OAuth は別です。 |
| TLS 接続に失敗 | 証明書チェーン、Mac の Python が参照する CA、プロキシを確認します。 |
| `ModuleNotFoundError: No module named 'yaml'` | 実行する仮想環境を有効にし、`python3 -m pip install -r slack-bolt/requirements.txt` と `python3 -m pip check` を実行します。 |
| GitHub の内容が違うと表示されるが `git diff` は空 | 旧 raw URL は以前の Blob を返す事例がありました。現行ツールは指定ブランチのコミットを Contents API で照合します。出力の両 Blob SHA、ファイルパス、改行、文字コードを確認します。 |
| Slack 承認通知が届かない | `./run-demo.sh --serve` の監視開始を確認してから通知 Job を実行します。Job が成功し、成果物に `demo_vulnerability_event` があることを確認します。旧版の通知 Playbook が同期されている場合は、成果物出力版に同期し、`restore-demo.sh` で Extra Variables を移行します。 |
| 候補がゼロ | AAP でデモ Workflow が実行中か、Check の成功後に Approval に到達したか、期限切れや処理済みでないか、サービスユーザーが参照・承認できるかを確認します。起動ツールは検索結果と理由を表示します。 |
| `--serve` が汎用エラーで終了 | 更新版の `slack_app.py --diagnose --workflow-job-id <ID> --approval-id <ID>` で、AAP の HTTP ステータス、Slack API コード、失敗段階を確認します。ID は実際の値を指定します。診断モードは Slack に投稿しません。 |
| `missing_scope`／`not_in_channel` | Bot の `chat:write`、Workspace、チャンネルへの招待を確認します。Socket Mode 側は App Token と `connections:write` を確認します。 |
| Slack のボタン操作が拒否される | 対応する専用通知、Workspace と許可ユーザー ID、承認状態を確認します。独立通知のボタンからは Approval を特定できません。 |
| 通知が記録済み、または投稿結果が未確定と表示 | 設定していたチャンネルと既定の状態 DB を確認します。保存済み通知なら再利用されます。結果不明なら Slack 上で両 ID を検索し、再投稿する前に既存通知を確認します。 |
| 旧設定に `apply_patch: true` がある | 現行の `aap-config/current-settings.yml` と AAP の両パッチテンプレートで `apply_patch: false` を確認します。復元ツールが検出した既存設定の競合は AAP 側で内容を確認して対応します。 |
| 統合デモで同期済み Playbook の照合に失敗 | 現行の読み取り専用 `rhel_patch.yml` が公開 GitHub にあり、AAP Project がその版へ同期済みかを確認します。Update Revision on Launch と Allow Branch Override は無効にします。 |
| 統合デモで Workflow 起動結果が未確定 | `./run-demo.sh --status` で実行履歴を読み取ります。起動結果を確認するまで自動再送は止まります。 |
| 統合デモで Slack 投稿や接続が失敗 | 承認前の通知や接続に失敗しても AAP は起動しません。保存済み通知があれば同じ設定で再利用します。起動後の結果は `--status` で確認します。 |
| Slack で承認した後に Check が失敗 | AAP の Check Job で対象ホスト、RHSA、有効な Inventory を確認します。Check が失敗すると Apply の成功経路には進みません。結果は元通知のスレッドと `--status` で確認できます。 |
