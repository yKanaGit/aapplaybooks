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

## 4. Lightspeed on Satellite 脆弱性通知機能の復元（オプション）

Lightspeed on Satellite (Technology Preview) を利用した脆弱性通知機能を復元する場合は、以下の手順を実施します。この機能は RHELパッチデモとは独立しており、オプションです。

### 4.1. Lightspeed on Satellite のセットアップ

RHDP環境のSatelliteにLightspeed on-premisesをセットアップします。**既存のSatellite設定を変更するため、新規環境または検証環境で実施してください。**

#### 前提条件

- Red Hat Satellite 6.18.3以降
- SSH経由でSatelliteとRHELホストにアクセス可能
- Bastion経由のSSH multi-hop構成（RHDP標準）
- Podmanコンテナ実行環境

#### セットアップ手順

1. **設定ファイルの作成**

   ```bash
   # サンプルをコピー
   cp config/lightspeed.env.example lightspeed.env
   
   # 編集（RHDP環境に合わせて値を設定）
   vi lightspeed.env
   ```

   主要な設定項目:
   - `BASTION_HOST`: Bastionホスト名またはIP
   - `BASTION_USER`: Bastionユーザー名
   - `SATELLITE_HOST`: Satellite内部ホスト名（通常: satellite）
   - `SATELLITE_USER`: Satelliteユーザー名（通常: ec2-user）
   - `NODE1_HOST`: RHELホスト名（Insights Client登録用）

2. **Dry-runで確認**

   ```bash
   ./setup-lightspeed.sh --dry-run --config lightspeed.env
   ```

   確認内容:
   - SSH接続確認
   - Satelliteバージョン確認（6.18.3以降）
   - 必要なリソース確認（CPU, メモリ, ディスク）
   - 既存DNS設定の確認

3. **セットアップ実行**

   ```bash
   ./setup-lightspeed.sh --config lightspeed.env
   ```

   実施される処理:
   - SatelliteのDNS設定修正（`satellite.example.com` → Satellite内部IP）
   - Container Registry認証設定
   - IOPコンテナのダウンロードとインストール
   - VMaaS CVE Mapデータダウンロード
   - Vulnerability Engineデータベース初期化
   - RHELホストへのInsights Client登録

4. **検証**

   ```bash
   # 検証のみ実行
   ./setup-lightspeed.sh --verify --config lightspeed.env
   ```

   確認項目:
   - IOPコンテナの起動状態
   - VMaaSサービスの応答
   - Vulnerability Engineの状態
   - Insights Client登録状態

#### 部分実行オプション

```bash
# Satelliteのみセットアップ（Insights Client登録をスキップ）
./setup-lightspeed.sh --satellite-only --config lightspeed.env

# Insights Clientのみセットアップ（Satellite設定をスキップ）
./setup-lightspeed.sh --client-only --config lightspeed.env
```

#### トラブルシューティング

- **DNS解決エラー**: `/etc/hosts`の設定を確認、必要に応じて手動修正
- **Registry認証失敗**: Red Hat Customer Portalの認証情報を確認
- **コンテナ起動失敗**: `podman ps -a`でコンテナ状態を確認、ログを確認
- **VMaaS同期失敗**: VMaaSコンテナのログを確認、リポジトリ同期状態を確認

詳細は [Lightspeedセットアップガイド](docs/lightspeed-setup.md) を参照してください。

### 4.2. AAP 脆弱性通知リソースのセットアップ

AAP 2.4にLightspeed on Satellite脆弱性通知用のリソースを作成します。

#### 前提条件

- AAP 2.4（Controller 4.5.x）以降
- OAuth トークン（write scope）
- Project: RHEL-Patch-Demo（または脆弱性通知Playbookを含むProject）
- Inventory: Slack-Notification-Inventory（localhost）
- Execution Environment: Default execution environment（Python 3含む）

#### セットアップ手順

1. **環境変数の設定**

   ```bash
   # AAP接続情報
   export AAP_URL="https://aap.example.com"
   export AAP_TOKEN="your-oauth-token"
   
   # Satellite Lightspeed API認証情報
   export SATELLITE_URL="https://satellite.example.com"
   export SATELLITE_USERNAME="admin"
   export SATELLITE_PASSWORD="your-password"
   export SATELLITE_ORGANIZATION_ID="1"
   export SATELLITE_LOCATION_ID="2"
   
   # Slack Incoming Webhook（オプション）
   export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
   ```

2. **Dry-runで確認**

   ```bash
   python3 setup-vulnerability-report.py --dry-run
   ```

   確認内容:
   - AAP接続確認
   - 既存リソースの検出
   - 作成予定のリソース一覧

3. **セットアップ実行**

   ```bash
   python3 setup-vulnerability-report.py
   ```

   作成されるリソース:
   - **Credential Type**: Satellite Lightspeed API, Slack Incoming Webhook
   - **Credential**: Satellite Lightspeed API, Slack Incoming Webhook
   - **Job Template**: RHEL-Vulnerability-Report

   既存リソースは再利用されます（冪等性確保）。

#### Job Templateの実行

AAP UIで `RHEL-Vulnerability-Report` を実行します。

**主要なExtra Variables**:

```yaml
# 取得モード
vulnerability_cve: ""              # 特定のCVE ID（空=top Nモード）
vulnerability_top: 10              # 上位N件（デフォルト: 10）

# 期間指定フィルター
vulnerability_date_filter: "none"  # none / first_detected / public_date
vulnerability_lookback_days: 7     # 遡り日数（デフォルト: 7）

# フィルター
vulnerability_content_view: ""             # Content View名
vulnerability_lifecycle_environment: ""    # Lifecycle Environment名

# Slack通知
vulnerability_send_slack: false    # Slack実送信（デフォルト: false = dry-run）

# TLS検証
satellite_insecure: true          # TLS証明書検証（demo環境ではtrue）
```

**実行例（過去7日以内の新規検知CVE）**:

```yaml
vulnerability_cve: ""
vulnerability_top: 10
vulnerability_date_filter: "first_detected"
vulnerability_lookback_days: 7
vulnerability_send_slack: false
```

#### 検証

```bash
# Playbook構文チェック
ansible-playbook satellite-vulnerability-report.yml --syntax-check

# Python単体テスト
python3 -m unittest discover -s satellite-vulnerability/tests -q

# Playbookテスト
./tests/test-vulnerability-report-playbook.sh
```

### 4.3. ELS（Extended Life Cycle Support）環境での注意事項

RHEL 7 ELS環境でLightspeed on Satelliteを使用する場合、以下の制限事項があります:

**制限事項**:
- Lightspeed構築後にELSリポジトリを追加した場合、Vulnerability EngineがELS CVEを自動検知しない可能性があります
- EvaluatorとGrouperの最適化ロジックにより、Content View変更が「システム変更」として認識されません

**推奨アプローチ**:
1. **新規環境**: Lightspeed構築**前**にELSリポジトリを有効化し、Content Viewを作成
2. **初回評価**: Lightspeed構築直後の初回評価でELS CVEが含まれることを確認

詳細は [ELS検証レポート](docs/els-vulnerability-investigation.md) を参照してください。

### 4.4. 未実装・未検証機能

以下の機能は未実装または未検証です:

- ⏳ **EDA統合**: Event-Driven Automationとの連携
- ⏳ **差分通知**: 前回取得結果との差分検知
- ⏳ **定期実行**: AAP Schedulerによる定期実行
- ⏳ **Workflow統合**: RHEL-Patch-Slack-Workflowとの統合
- ⏳ **ELS CVE検知**: 既存環境でのELSリポジトリ追加後の自動検知

これらの機能が必要な場合は、個別に検証と実装が必要です。
