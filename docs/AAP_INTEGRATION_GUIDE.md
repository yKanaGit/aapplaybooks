# AAP統合ガイド - Lightspeed on Satellite脆弱性レポート

## 概要

このガイドでは、Lightspeed on Satelliteの脆弱性情報取得とSlack通知処理をAAP 2.4のJob Templateとして実行する方法を説明します。

## 前提条件

- AAP 2.4が稼働していること
- Satellite 6.18.3でLightspeed on-premisesが構築済みであること
- 管理対象RHELシステムがSatelliteに登録されていること
- Slack Incoming Webhookが作成済みであること

## アーキテクチャ

```
AAP Controller
  ↓ (Job Template実行)
  ↓
Execution Environment (localhost)
  ├─ satellite_vulnerability.py
  │   ↓ (HTTPS/Basic Auth)
  │   ↓
  │  Satellite Vulnerability API
  │   ↓
  │  JSON出力
  │
  └─ slack_notify.py
      ↓ (HTTPS/Webhook)
      ↓
     Slack
```

## 構成ファイル

```
aapplaybooks/
├── satellite-vulnerability-report.yml  # メインPlaybook
├── satellite_vulnerability.py          # 脆弱性取得スクリプト
├── slack_notify.py                     # Slack通知スクリプト
├── setup-vulnerability-report.sh       # セットアップスクリプト
└── aap-resources/
    ├── credential-types/
    │   ├── satellite-lightspeed-api.yml
    │   └── slack-incoming-webhook.yml
    └── job-templates/
        └── rhel-vulnerability-report.yml
```

## セットアップ手順

### 1. Credential Typeの作成

#### Satellite Lightspeed API

1. AAP UIで **Resources > Credential Types** に移動
2. **Add** をクリック
3. 以下を設定：

**Name**: `Satellite Lightspeed API`

**Description**:
```
Authentication for Satellite Lightspeed on-premises Vulnerability API.
Provides organization and location context for CVE queries.
```

**Input Configuration**:
```yaml
fields:
  - id: satellite_url
    type: string
    label: Satellite URL
    help_text: "Base URL of Satellite server (e.g., https://satellite.example.com)"
  - id: satellite_username
    type: string
    label: Username
    help_text: "Satellite username with Vulnerability API access"
  - id: satellite_password
    type: string
    label: Password
    secret: true
  - id: satellite_organization_id
    type: string
    label: Organization ID
    help_text: "Satellite Organization ID (e.g., 1)"
  - id: satellite_location_id
    type: string
    label: Location ID
    help_text: "Satellite Location ID (e.g., 2)"
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
  SATELLITE_URL: '{{ satellite_url }}'
  SATELLITE_USERNAME: '{{ satellite_username }}'
  SATELLITE_PASSWORD: '{{ satellite_password }}'
  SATELLITE_ORGANIZATION_ID: '{{ satellite_organization_id }}'
  SATELLITE_LOCATION_ID: '{{ satellite_location_id }}'
```

#### Slack Incoming Webhook

1. AAP UIで **Resources > Credential Types** に移動
2. **Add** をクリック
3. 以下を設定：

**Name**: `Slack Incoming Webhook`

**Description**:
```
Slack Incoming Webhook URL for sending notifications.
```

**Input Configuration**:
```yaml
fields:
  - id: slack_webhook_url
    type: string
    label: Webhook URL
    help_text: "Slack Incoming Webhook URL (https://hooks.slack.com/services/...)"
    secret: true
required:
  - slack_webhook_url
```

**Injector Configuration**:
```yaml
env:
  SLACK_WEBHOOK_URL: '{{ slack_webhook_url }}'
```

### 2. Credentialの作成

#### Satellite Lightspeed API Credential

1. **Resources > Credentials** に移動
2. **Add** をクリック
3. 以下を設定：
   - **Name**: `Satellite Lightspeed API`
   - **Organization**: `Default`
   - **Credential Type**: `Satellite Lightspeed API`
   - **Satellite URL**: `https://satellite.zswk7.sandbox1268.opentlc.com`
   - **Username**: `admin`
   - **Password**: `<your-password>`
   - **Organization ID**: `1`
   - **Location ID**: `2`

#### Slack Incoming Webhook Credential

1. **Resources > Credentials** に移動
2. **Add** をクリック
3. 以下を設定：
   - **Name**: `Slack Incoming Webhook`
   - **Organization**: `Default`
   - **Credential Type**: `Slack Incoming Webhook`
   - **Webhook URL**: `https://hooks.slack.com/services/YOUR/WEBHOOK/URL`

### 3. Inventoryの確認

既存の `Slack-Notification-Inventory` を使用します。

存在しない場合は作成：
1. **Resources > Inventories** に移動
2. **Add > Add inventory** をクリック
3. 以下を設定：
   - **Name**: `Slack-Notification-Inventory`
   - **Organization**: `Default`

4. Inventoryを開き、**Hosts > Add** をクリック
5. 以下を設定：
   - **Name**: `localhost`
   - **Variables**:
     ```yaml
     ansible_connection: local
     ansible_python_interpreter: '{{ ansible_playbook_python }}'
     ```

### 4. Job Templateの作成

1. **Resources > Templates** に移動
2. **Add > Add job template** をクリック
3. 以下を設定：

- **Name**: `RHEL-Vulnerability-Report`
- **Job Type**: `Run`
- **Inventory**: `Slack-Notification-Inventory`
- **Project**: `RHEL-Patch-Demo` (aapplaybooksを含むProject)
- **Playbook**: `satellite-vulnerability-report.yml`
- **Execution Environment**: `Default execution environment`
- **Credentials**:
  - `Satellite Lightspeed API`
  - `Slack Incoming Webhook`

4. **Variables** セクションで **Prompt on launch** をチェック

5. **Extra Variables** に以下を設定（デフォルト値）:
```yaml
vulnerability_cve: ""
vulnerability_top: 10
vulnerability_content_view: ""
vulnerability_lifecycle_environment: ""
vulnerability_send_slack: false
satellite_insecure: true
```

6. **Save** をクリック

## 使用方法

### 単一CVEの脆弱性レポート取得（ドライラン）

1. Job Template `RHEL-Vulnerability-Report` を開く
2. **Launch** をクリック
3. Extra Variablesを編集：
```yaml
vulnerability_cve: "CVE-2024-2961"
vulnerability_send_slack: false
satellite_insecure: true
```
4. **Next > Launch** をクリック

### 上位10件のCVE取得

```yaml
vulnerability_cve: ""
vulnerability_top: 10
vulnerability_send_slack: false
satellite_insecure: true
```

### Content ViewとLifecycle Environmentで絞り込み

```yaml
vulnerability_cve: "CVE-2024-2961"
vulnerability_content_view: "RHEL7"
vulnerability_lifecycle_environment: "RHEL7_Dev"
vulnerability_send_slack: false
satellite_insecure: true
```

### Slackへの実送信

```yaml
vulnerability_cve: "CVE-2024-2961"
vulnerability_send_slack: true
satellite_insecure: true
```

## ジョブ出力の確認

Job実行後、以下の情報がジョブログに出力されます：

```
TASK [Display vulnerability summary]
ok: [localhost] => {
    "msg": "Data source: satellite\nGenerated at: 2026-11-27T...\nMode: cve\nCVEs found: 1\nTotal CVEs in Satellite: 40\nSystem count: 2\n"
}

TASK [Display CVE details (single CVE mode)]
ok: [localhost] => (item=CVE-2024-2961) => {
    "msg": "CVE ID: CVE-2024-2961\nImpact: Important\nCVSS v3 Score: 8.800\nAffected hosts: 2\nHosts with advisory: 2\n"
}
```

## トラブルシューティング

### 認証エラー

**エラー**: `SATELLITE_URL が必要です`

**対処**: Satellite Lightspeed API Credentialが正しく設定されているか確認

### Satellite API接続エラー

**エラー**: `Failed to fetch vulnerability data from Satellite`

**対処**:
1. SatelliteのURLが正しいか確認
2. Execution EnvironmentからSatelliteへのHTTPS疎通を確認
3. 認証情報（Username/Password）が正しいか確認

### Slack送信エラー

**エラー**: `Slack Webhook の送信に失敗しました`

**対処**:
1. Webhook URLが正しいか確認
2. Execution EnvironmentからSlackへのHTTPS疎通を確認

### Top N mode不対応エラー

**エラー**: `Slack notification currently supports single CVE mode only`

**対処**: Slack通知を使用する場合は、`vulnerability_cve` パラメータで特定のCVEを指定してください。

## 制約事項

### 現在の制約

1. **Slack通知は単一CVEモードのみ対応**
   - `vulnerability_cve` を指定する必要があります
   - Top Nモードではドライランのみ実行されます

2. **差分検知機能は未実装**
   - 毎回すべての脆弱性情報を取得します
   - 前回からの差分通知は将来実装予定です

3. **Scheduler/Event駆動は未実装**
   - 手動実行のみです
   - AAP SchedulerやEvent-Driven Ansibleとの統合は将来実装予定です

### 今後の拡張

- 週間スケジュールによる差分報告
- Event-Driven Ansible (ansible-rulebook) 連携
- 複数CVEのSlack通知対応
- パッチ適用ワークフローとの統合

## セキュリティ考慮事項

- 認証情報はAAP Credentialで管理され、Playbookやログに出力されません
- Webhook URLはsecretフィールドとして保護されます
- 一時ファイルはジョブ終了時に自動削除されます
- TLS証明書検証はデフォルトで有効（`satellite_insecure: false`）

## 関連ドキュメント

- `docs/satellite-vulnerability.md`: Satellite脆弱性取得スクリプトの詳細
- `docs/SATELLITE_ENHANCEMENT_REPORT.md`: Satellite版機能拡張報告
- `docs/BUGFIX_REPORT.md`: Bug修正報告
- `aap-resources/credential-types/`: Credential Type定義
- `aap-resources/job-templates/`: Job Template定義

---

**作成日**: 2024年11月27日  
**対象AAP**: 2.4  
**対象Satellite**: 6.18.3
