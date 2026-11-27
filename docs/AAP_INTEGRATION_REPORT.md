# AAP統合完了報告 - Lightspeed on Satellite脆弱性レポート

## 実施日
2024年11月27日

## 概要

Lightspeed on Satelliteの脆弱性情報取得とSlack通知処理をAAP 2.4のJob Templateから実行できるように統合しました。

## 作成・変更したファイル

### 1. メインPlaybook

#### satellite-vulnerability-report.yml（新規作成、約200行）
**場所**: `aapplaybooks/satellite-vulnerability-report.yml`

**主な機能**:
- hosts: localhost、connection: local、gather_facts: false
- Satellite Lightspeed APIから脆弱性情報を取得
- Slack通知（ドライランまたは実送信）
- 一時ファイルの自動削除
- 認証情報の環境変数経由注入
- エラーハンドリングと詳細ログ出力

**処理フロー**:
1. 環境変数（認証情報）の検証
2. 入力パラメータの検証
3. satellite_vulnerability.pyの実行
4. JSON出力の読み込みと検証
5. 脆弱性情報の概要表示
6. slack_notify.pyの実行（ドライランまたは実送信）
7. 一時ファイルの削除

**実行パラメータ**:
- `vulnerability_cve`: 特定CVE ID（空の場合はTop Nモード）
- `vulnerability_top`: 上位N件（デフォルト: 10）
- `vulnerability_content_view`: Content Viewフィルタ（任意）
- `vulnerability_lifecycle_environment`: Lifecycle Environmentフィルタ（任意）
- `vulnerability_send_slack`: Slack実送信（デフォルト: false）
- `satellite_insecure`: TLS証明書検証無効化（デフォルト: false）

### 2. AAP Custom Credential Types

#### aap-resources/credential-types/satellite-lightspeed-api.yml（新規作成）
**内容**: Satellite Lightspeed API認証情報の定義

**フィールド**:
- satellite_url（必須）
- satellite_username（必須）
- satellite_password（必須、secret）
- satellite_organization_id（必須）
- satellite_location_id（必須）

**環境変数注入**:
- SATELLITE_URL
- SATELLITE_USERNAME
- SATELLITE_PASSWORD
- SATELLITE_ORGANIZATION_ID
- SATELLITE_LOCATION_ID

#### aap-resources/credential-types/slack-incoming-webhook.yml（新規作成）
**内容**: Slack Incoming Webhook URLの定義

**フィールド**:
- slack_webhook_url（必須、secret）

**環境変数注入**:
- SLACK_WEBHOOK_URL

### 3. AAP Job Template定義

#### aap-resources/job-templates/rhel-vulnerability-report.yml（新規作成）
**内容**: Job Templateの設定内容

**主要設定**:
- Name: RHEL-Vulnerability-Report
- Inventory: Slack-Notification-Inventory
- Project: RHEL-Patch-Demo
- Playbook: satellite-vulnerability-report.yml
- Execution Environment: Default execution environment
- Credentials: Satellite Lightspeed API, Slack Incoming Webhook
- ask_variables_on_launch: true

### 4. セットアップスクリプト

#### setup-vulnerability-report.sh（新規作成、約190行）
**場所**: `aapplaybooks/setup-vulnerability-report.sh`

**機能**:
- Credential Typeの存在確認
- Inventoryの存在確認
- セットアップ手順のガイダンス表示
- dry-runモードのサポート

**使用方法**:
```bash
./setup-vulnerability-report.sh [--dry-run]
```

### 5. ドキュメント

#### docs/AAP_INTEGRATION_GUIDE.md（新規作成、約450行）
**内容**:
- アーキテクチャ説明
- セットアップ手順（詳細）
- Credential Typeの作成手順
- Credentialの作成手順
- Job Templateの作成手順
- 使用方法とパラメータ例
- トラブルシューティング
- 制約事項と今後の拡張

### 6. テストスクリプト

#### tests/test-vulnerability-report-playbook.sh（新規作成、約200行）
**場所**: `aapplaybooks/tests/test-vulnerability-report-playbook.sh`

**テスト項目**（全10件）:
1. Playbook構文チェック
2. 必要ファイルの存在確認
3. Pythonスクリプトのインポート確認
4. AAPリソースファイルの存在確認
5. YAML構文の検証
6. ドキュメントの存在確認
7. セットアップスクリプトの実行可能性確認
8. セットアップスクリプトのdry-run
9. Python単体テストの実行
10. 認証情報のハードコード確認

### 7. 変更したファイル

#### satellite_vulnerability.py
- 実行可能権限を追加（chmod +x）

#### slack_notify.py
- 実行可能権限を追加（chmod +x）

## Playbookの処理概要

### 実行フロー

```
1. 環境変数検証
   ├─ SATELLITE_URL
   ├─ SATELLITE_USERNAME
   ├─ SATELLITE_PASSWORD
   ├─ SATELLITE_ORGANIZATION_ID
   ├─ SATELLITE_LOCATION_ID
   └─ SLACK_WEBHOOK_URL（Slack送信時のみ）

2. パラメータ検証
   ├─ vulnerability_top: 1-100の数値
   ├─ vulnerability_send_slack: boolean
   └─ satellite_insecure: boolean

3. 脆弱性情報取得
   ├─ satellite_vulnerability.py実行
   │   ├─ --cve または --top
   │   ├─ --content-view（任意）
   │   ├─ --lifecycle-environment（任意）
   │   ├─ --insecure（任意）
   │   └─ --output（一時ディレクトリ）
   ├─ JSON読み込み
   ├─ completeness検証
   └─ 概要表示

4. Slack通知
   ├─ 単一CVEモードの確認
   ├─ slack_notify.py --dry-run
   ├─ ペイロード表示
   └─ slack_notify.py（実送信、条件付き）

5. クリーンアップ
   └─ 一時ディレクトリ削除
```

### エラーハンドリング

- **脆弱性取得失敗**: Slack通知を抑止してジョブ失敗
- **complete=false**: アサーションエラーでジョブ失敗
- **Top NモードでSlack送信**: アサーションエラーで説明
- **認証情報不足**: アサーションエラーで説明

### ログ出力

**ジョブログに出力される情報**:
- 実行モード（Single CVE / Top N CVEs）
- フィルタ設定（Content View / Lifecycle Environment）
- Slack通知の有効/無効
- TLS検証の有効/無効
- 脆弱性情報の概要（データソース、生成日時、CVE数）
- CVE詳細（影響度、CVSSスコア、影響ホスト数）
- Slackペイロード（dry-run時）
- 通知結果（実送信時）

**ログに出力されない情報**:
- パスワード（環境変数のみ、no_log不要）
- Webhook URL（環境変数のみ）

## Custom Credential Typeの設定内容

### Satellite Lightspeed API

**Input Configuration**:
```yaml
fields:
  - id: satellite_url
    type: string
    label: Satellite URL
  - id: satellite_username
    type: string
    label: Username
  - id: satellite_password
    type: string
    label: Password
    secret: true
  - id: satellite_organization_id
    type: string
    label: Organization ID
  - id: satellite_location_id
    type: string
    label: Location ID
required:
  - satellite_url
  - satellite_username
  - satellite_password
  - satellite_organization_id
  - satellite_location_id
```

**Injector Configuration**:
```yaml
env:
  SATELLITE_URL: "{{ satellite_url }}"
  SATELLITE_USERNAME: "{{ satellite_username }}"
  SATELLITE_PASSWORD: "{{ satellite_password }}"
  SATELLITE_ORGANIZATION_ID: "{{ satellite_organization_id }}"
  SATELLITE_LOCATION_ID: "{{ satellite_location_id }}"
```

### Slack Incoming Webhook

**Input Configuration**:
```yaml
fields:
  - id: slack_webhook_url
    type: string
    label: Webhook URL
    secret: true
required:
  - slack_webhook_url
```

**Injector Configuration**:
```yaml
env:
  SLACK_WEBHOOK_URL: "{{ slack_webhook_url }}"
```

## Job Templateの設定内容

| 項目 | 設定値 |
|------|--------|
| Name | RHEL-Vulnerability-Report |
| Organization | Default |
| Job Type | Run |
| Inventory | Slack-Notification-Inventory |
| Project | RHEL-Patch-Demo |
| Playbook | satellite-vulnerability-report.yml |
| Execution Environment | Default execution environment |
| Credentials | Satellite Lightspeed API<br>Slack Incoming Webhook |
| Ask variables on launch | Yes |

**Default Extra Variables**:
```yaml
vulnerability_cve: ""
vulnerability_top: 10
vulnerability_content_view: ""
vulnerability_lifecycle_environment: ""
vulnerability_send_slack: false
satellite_insecure: true
```

## テスト結果

### 自動テスト結果

```
Ran 10 tests in 0.5s
PASSED: 10/10

✓ Playbook構文チェック
✓ 必要ファイルの存在確認
✓ Pythonスクリプトのインポート
✓ AAPリソースファイルの存在
✓ YAML構文の検証
✓ ドキュメントの存在確認
✓ セットアップスクリプトの確認
✓ セットアップスクリプトdry-run
✓ Python単体テスト（38/38成功）
✓ 認証情報のハードコード確認
```

### 手動検証項目（未実施）

以下は実環境での検証が必要です：

⏳ **AAP環境でのPlaybook実行**
- Credential Type作成
- Credential作成
- Inventory確認/作成
- Job Template作成
- Job実行

⏳ **Execution Environment内での動作確認**
- Python依存関係の確認
- SatelliteへのHTTPS疎通
- SlackへのHTTPS疎通

⏳ **エンドツーエンドテスト**
- 脆弱性情報取得
- JSON出力検証
- Slack通知（ドライラン）
- Slack通知（実送信）

## AAPへの登録手順

### 前提条件

- AAP 2.4が稼働している
- aapplaybooksリポジトリがProjectとして登録されている
- Satellite Lightspeed on-premisesが構築済み
- Slack Incoming Webhookが作成済み

### 手順

#### 1. Credential Type作成

```bash
# AAP UIでの作業
Resources > Credential Types > Add

# satellite-lightspeed-api.ymlの内容を参照して作成
# slack-incoming-webhook.ymlの内容を参照して作成
```

#### 2. Credential作成

```bash
# Satellite Lightspeed API Credential
Resources > Credentials > Add
  Name: Satellite Lightspeed API
  Credential Type: Satellite Lightspeed API
  Satellite URL: https://satellite.zswk7.sandbox1268.opentlc.com
  Username: admin
  Password: <your-password>
  Organization ID: 1
  Location ID: 2

# Slack Incoming Webhook Credential
Resources > Credentials > Add
  Name: Slack Incoming Webhook
  Credential Type: Slack Incoming Webhook
  Webhook URL: https://hooks.slack.com/services/...
```

#### 3. Inventory確認

```bash
# 既存のSlack-Notification-Inventoryを確認
# 存在しない場合は作成:
Resources > Inventories > Add
  Name: Slack-Notification-Inventory
  Organization: Default

# localhost Host追加
Hosts > Add
  Name: localhost
  Variables:
    ansible_connection: local
    ansible_python_interpreter: '{{ ansible_playbook_python }}'
```

#### 4. Job Template作成

```bash
# rhel-vulnerability-report.ymlの内容を参照
Resources > Templates > Add > Add job template
  # 設定内容は上記「Job Templateの設定内容」を参照
```

#### 5. 動作確認

```bash
# Job Template実行
1. RHEL-Vulnerability-Report を開く
2. Launch をクリック
3. Extra Variablesを確認/編集
4. Launch をクリック
```

## 未検証事項

### AAP環境関連

⏳ **Credential Type作成**: AAP UIでの実作業が必要  
⏳ **Credential作成**: 実際の認証情報の設定が必要  
⏳ **Job Template作成**: AAP UIでの実作業が必要  
⏳ **Job実行**: AAP環境での動作確認が必要

### 機能関連

⏳ **Top Nモード**: 複数CVEの取得とログ表示  
⏳ **Content View/Lifecycle Environmentフィルタ**: 実環境での動作確認  
⏳ **Slack実送信**: 実際のSlack Channelへの送信  
⏳ **エラーハンドリング**: 各種エラーケースでの動作

### 環境関連

⏳ **Execution Environment**: Python依存関係の充足確認  
⏳ **ネットワーク疎通**: Satellite/Slackへのアクセス可否  
⏳ **Credential注入**: 環境変数の正しい注入  

## 次のステップ

### 短期（基本機能の確認）

1. **AAP環境へのリソース登録**
   - Credential Type作成
   - Credential作成
   - Job Template作成

2. **基本動作確認**
   - 単一CVEの取得（ドライラン）
   - Slack通知（ドライラン）
   - Slack通知（実送信）

3. **エラーケースの確認**
   - 認証エラー
   - ネットワークエラー
   - パラメータエラー

### 中期（運用機能の追加）

4. **差分検知機能の実装**
   - 前回取得結果の永続化
   - 差分抽出ロジック
   - 差分のみのSlack通知

5. **AAP Schedulerとの統合**
   - 週間スケジュール設定
   - 差分レポートの定期送信

### 長期（Event駆動の実装）

6. **Event-Driven Ansibleとの統合**
   - ansible-rulebookの実装
   - Lightspeed CVE更新イベントの検知
   - Content View昇格イベントの検知
   - 自動Slack通知

7. **パッチ適用ワークフローとの統合**
   - Slack通知からのパッチ適用承認
   - 既存RHEL-Patch-Applyワークフローへの連携

## 作業対象外（今回実施しなかった項目）

✅ **差分検知機能**: 将来実装予定  
✅ **前回取得結果の永続化**: 将来実装予定  
✅ **AAP Scheduler設定**: 将来実装予定  
✅ **ansible-rulebook構築**: 将来実装予定  
✅ **LightspeedのCVE情報更新イベント検知**: 将来実装予定  
✅ **Content View昇格イベント検知**: 将来実装予定  
✅ **パッチ適用ワークフローとの統合**: 将来実装予定  
✅ **実環境でのAAP設定変更**: 手順書のみ提供  
✅ **Gitへのpush**: 作業対象外  
✅ **Slackへの実送信**: テスト環境なし

## まとめ

### 実施した作業

✅ **AAP用Playbook作成**: satellite-vulnerability-report.yml  
✅ **Custom Credential Type定義**: 2種類  
✅ **Job Template定義**: RHEL-Vulnerability-Report  
✅ **セットアップスクリプト**: setup-vulnerability-report.sh  
✅ **統合ガイド**: docs/AAP_INTEGRATION_GUIDE.md  
✅ **テストスクリプト**: tests/test-vulnerability-report-playbook.sh  
✅ **自動テスト**: 10/10成功

### 成果物

- **Playbook**: 1ファイル（約200行）
- **Credential Type定義**: 2ファイル
- **Job Template定義**: 1ファイル
- **セットアップスクリプト**: 1ファイル（約190行）
- **ドキュメント**: 2ファイル（約700行）
- **テストスクリプト**: 1ファイル（約200行）

### 保証される動作

✅ Playbook構文の正当性  
✅ Python依存関係の充足（ローカル環境）  
✅ YAML構文の正当性  
✅ 認証情報の安全な取り扱い  
✅ 既存Python機能の互換性

### 未検証事項

⏳ AAP環境での実行  
⏳ Execution Environmentでの動作  
⏳ Satellite/Slackへの実疎通  
⏳ エラーケースの網羅的確認

---

**実施者**: Claude Sonnet 4.5  
**実施日**: 2024年11月27日  
**対象AAP**: 2.4  
**対象Satellite**: 6.18.3
