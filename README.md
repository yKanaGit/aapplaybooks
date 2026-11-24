# Red Hat AAP RHEL パッチデモの復元

macOS の zsh から `./restore-demo.sh` を実行すると、AAP のデモ設定を復元できます。AAP 2.4（Controller 4.5.x）の従来型 API と、AAP 2.5／2.6（Controller 4.6.x／4.7.x）の Gateway 経由の API に対応しています。

## 統合デモ

復元と設定が済んだら、Mac から `./run-demo.sh --dry-run` で確認し、`./run-demo.sh --serve` を起動します。AAP 管理画面から `RHEL-Slack-Notification` を実行するたびに、Slack 承認から結果通知までのデモを繰り返せます。 [統合デモと復元手順](RESTORE.md)

## 運用ドキュメント

- [構成とファイル分類](ARCHITECTURE.md)
- [Mac の初回準備](SETUP.md)
- [新しい OpenTLC 環境への復元](RESTORE.md)
- [設定と秘密情報の管理](CONFIGURATION.md)
- [トラブルシューティング](TROUBLESHOOTING.md)

## 初回セットアップ

1. macOS の Bash と Python 3.10 以降が必要です。下記の例は Python 3.12 と ansible-core 2.20 を使用します。
2. AAP に対象 Organization、通常の RHEL Inventory と有効な RHEL ホスト、その Organization の Machine（SSH）Credential、対象 Organization または全体で利用可能な Execution Environment を用意します。不足リソースの作成権限と参照権限を持つアカウントを使い、接続先の Gateway または従来型 Controller で書き込みスコープ付き OAuth アクセストークンを発行します。Credential Type の新規作成には管理者権限が必要です。
3. 公開 GitHub リポジトリーの `main` ブランチに、この作業ツリーと一致する `rhel_patch.yml` と `slack_notify.yml` を用意します。ブランチは `aap-config/current-settings.yml` の `project.scm_branch` で指定します。Mac と AAP の両方から GitHub に接続できる必要があります。

```zsh
python3.12 -m venv "$HOME/.venvs/aap-demo-restore"
source "$HOME/.venvs/aap-demo-restore/bin/activate"
python3 -m pip install 'ansible-core>=2.20,<2.21'
python3 -m pip install -r slack-bolt/requirements.txt
python3 -m pip check
```

リポジトリーのルートで上記を実行してから復元を開始します。Slack Bolt の依存ファイルには、復元ツールと Slack Bolt の両方が `import yaml` で使う PyYAML が含まれています。後から別の仮想環境で Slack Bolt を起動する場合は、その環境でも依存ファイルをインストールしてください。

必要なモジュールは `ansible.builtin` に含まれます。追加 Collection は空の `rhel-patch-demo-restore/requirements.yml` に明示しています。各起動でローカルの Ansible 構文とモジュール解決を検証します。復元は検出した Controller REST API を使用し、既存オブジェクトを更新し得る従来の `awx.awx` モジュール呼び出しと、複数サービス対象の `infra.aap_configuration.dispatch` は実行経路から外しています。`restore_aap.yml` は既定でローカル定義を検証し、実行モードを指定すると同じ対話式ツールを呼び出します。 [Controller REST API](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.6/develop-assembly_controller_api_browsing_api) · [Credential Type](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.6/secure-assembly_controller_custom_credentials) · [AAP API の変更](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.6/upgrade-assembly_upgrade_api_changes) · [旧 AWX 24.6.1 Job Template モジュール](https://github.com/ansible/awx/blob/24.6.1/awx_collection/plugins/modules/job_template.py) · [旧 AWX 24.6.1 Workflow Node モジュール](https://github.com/ansible/awx/blob/24.6.1/awx_collection/plugins/modules/workflow_job_template_node.py) · [Configuration as Code の設定形式](https://github.com/redhat-cop/infra.aap_configuration/blob/devel/docs/GETTING_STARTED.md)

## 実行

初回セットアップで使用した仮想環境を有効にします。

```zsh
source "$HOME/.venvs/aap-demo-restore/bin/activate"
./restore-demo.sh --dry-run
./restore-demo.sh
```

AAP のベース URL、OAuth トークン、Organization、Inventory、ホスト名、SSH Credential、Execution Environment、GitHub URL の順に入力します。`Default`、`Workshop Inventory`、`node1.example.com` などの既定値は Enter で選べます。新規作成予定と再利用予定を確認し、本実行で作成と通知変数の更新を承認する場合は `RESTORE` と入力してください。新規 Project の初回 SCM 同期は AAP が自動で開始します。ツールは同期結果を読み取り、プレイブックが揃ってから残りのリソースを作成します。

接続先の判定では、まず `GET /api/controller/v2/ping/` を試し、HTTP 404 の場合に `GET /api/v2/ping/` を試します。判定した API の `me/` で OAuth 認証を確認し、以降の読み取りと承認済みの作成処理で同じ API を使用します。 [AAP 2.4 Controller API ガイド](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.4/html-single/automation_controller_api_overview/automation_controller_api_overview)

端末から Ansible Playbook を直接実行する場合も、同じ対話入力と承認手順を利用できます。

```zsh
ansible-playbook -i localhost, rhel-patch-demo-restore/restore_aap.yml \
  -e restore_demo_mode=dry-run -e "restore_demo_tty=$(tty)"
ansible-playbook -i localhost, rhel-patch-demo-restore/restore_aap.yml \
  -e restore_demo_mode=restore -e "restore_demo_tty=$(tty)"
```

Dry Run は GitHub と AAP に GET のみ送信します。Ansible Check Mode を使用せず、承認前および Dry Run 中は API クライアントが書き込みを拒否します。完全に一致する構成は再実行時に再利用します。`RHEL-Slack-Notification` の Extra Variables の差分は更新予定に表示します。それ以外の既存設定に競合がある場合は停止します。既存設定に含まれ得る秘密値は差分表示でマスクします。

### 認証情報

OAuth トークンは端末で非表示入力し、選択した Controller API の Authorization ヘッダーで使用します。Slack の投稿は Mac の Slack Bolt が行います。SSH 秘密鍵は AAP の既存 Credential に保持します。

## 復元範囲

| リソース | 名前・設定 |
| --- | --- |
| Project | `RHEL-Patch-Demo` |
| Slack Inventory と Host | `Slack-Notification-Inventory`、`localhost` |
| Job Template | `RHEL-Patch-Check`、`RHEL-Patch-Apply`、`RHEL-Slack-Notification` |
| Workflow と Nodes | 統合用 `RHEL-Patch-Slack-Workflow` は Check → Apply → Verify。手動用 `RHEL-Patch-Workflow` は Check → AAP Approval → Apply |

Check・Apply・Verify に `apply_patch: false` を設定します。Organization、RHEL Inventory とそのホスト、SSH Credential、Execution Environment は既存のものを再利用します。Schedule、EDA、Slack Bolt の起動、Satellite の設定、通知テンプレートの Extra Variables と統合 Workflow の Verify 追加以外の既存設定の更新、および既存リソースの削除、ジョブやワークフローの起動は復元対象外です。`RHEL-Slack-Notification` は Extra Variables の `demo_event` を AAP のジョブ成果物に出力します。既存の Webhook Credential は保持します。起動済みの AAP Workflow を Slack の専用通知から承認する手順は [Slack Bolt 連携](slack-bolt/README.md) を参照してください。

## トラブルシューティング

- HTTPS エラーの場合は Gateway または Controller のベース URL、証明書チェーン、ネットワークを確認します。社内 CA を使う場合は、公開ルートも含む信頼済み CA バンドルを Python の `SSL_CERT_FILE` に指定できます。
- HTTP 401 や 403 の場合は接続先で発行した OAuth トークンの有効期限、スコープ、対象リソースへのアクセス権を確認します。
- GitHub の照合では、指定ブランチのコミット SHA を取得し、そのコミットを指定して Contents API で両プレイブックを読み取ります。不一致時は GitHub 側のリポジトリー、ブランチ、ファイルパス、コミット SHA と、ローカルの絶対パスに加え、両方の Git Blob SHA、改行と文字コードの情報を表示します。照合は作業ツリーのバイト列を使うため、Git の改行変換などによって `git diff` の結果と異なることがあります。非公開 GitHub と GitHub Enterprise は未対応です。
- 既存の Slack Inventory に localhost がない場合や Workflow が部分構成の場合は競合として停止します。中断や API エラーで作成が途中まで進んだ場合は、結果に出たリソースと AAP の状態を確認し、必要な不足分を AAP で確認・調整してから再度 Dry Run を実行します。一連の API 作成にはトランザクションがないため、途中作成されたリソースは残ります。
- 通知が表示されない場合は、Mac サービスの監視開始後に AAP から通知 Job を起動したか、Job が成功し `demo_vulnerability_event` を出力したかを確認します。Dry Run で Project の同期 Revision と通知テンプレートの差分を照合できます。
- 初回 SCM 同期が失敗した場合は AAP の Project の同期結果を確認します。手動で同期を完了すれば、次回実行では一致する Project を再利用できます。

## 復元後の確認とデモ後の停止

1. AAP で Project の同期結果、各 Job Template の Inventory・Credential・Execution Environment、両パッチテンプレートの `apply_patch: false` を確認します。
2. 統合用 Workflow の Check → Apply → Verify と、手動用 Workflow の Approval ノードを確認します。
3. 動作確認が必要な場合は、手動で `RHEL-Patch-Check` と `RHEL-Slack-Notification` を個別に実行します。RHEL に接続する前に、対象ホスト、SSH 権限、yum の利用可否、EE と管理対象の Python の互換性を確認します。
4. デモ終了時に手動で起動したジョブやワークフローが残っていれば、AAP の Jobs 画面からキャンセルします。デモ専用の OAuth トークンや Slack Webhook が不要になった場合は、各サービスで失効させます。

## ローカル検証

```zsh
shellcheck restore-demo.sh
bash -n restore-demo.sh
python -B -m unittest discover -s rhel-patch-demo-restore -v
ansible-playbook -i localhost, rhel-patch-demo-restore/restore_aap.yml
ansible-playbook -i localhost, --syntax-check rhel_patch.yml
ansible-playbook -i localhost, --syntax-check slack_notify.yml
```
