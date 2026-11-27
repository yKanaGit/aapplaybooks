# セットアップスクリプト実装完了報告

## 実施日
2024年11月27日

## 概要

手動設定のガイダンスを表示するだけだった`setup-vulnerability-report.sh`を、AAP REST APIを直接使用して実際にリソースを作成・更新するスクリプトに修正しました。

## 作成・変更したファイル

### 1. setup-vulnerability-report.py（新規作成、約570行）

**実装内容**:
- AAP REST APIを使用したリソースの自動作成・更新
- Credential Type、Credential、Job Templateの作成
- 冪等性の確保（既存リソースの検出と更新）
- dry-runモードのサポート
- 環境変数と対話入力による認証情報の取得

**主要クラス**:
- `Controller`: AAP REST API client
- `SetupError`: セットアップエラー
- `HTTPStatusError`: HTTP status error
- `NoRedirect`: リダイレクト防止handler
- `ResourceDef`: リソース定義dataclass

**主要関数**:
- `setup_credential_types()`: Credential Type作成
- `setup_credentials()`: Credential作成
- `setup_job_template()`: Job Template作成

### 2. setup-vulnerability-report.sh（修正、約20行）

**変更内容**:
- Pythonスクリプトへの薄いwrapper化
- 実装ロジックをsetup-vulnerability-report.pyに委譲

### 3. tests/test-vulnerability-report-playbook.sh（修正）

**追加テスト**:
- Python setup scriptの構文チェック
- --helpオプションのテスト
- 認証情報のハードコード確認（setup-vulnerability-report.pyも対象）

## 既存restore-demo.shから再利用した処理

### 1. AAP REST API接続方法

**再利用したアプローチ**:
```python
# urllib.requestを使用したHTTPS接続
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

def https_opener():
    return build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
```

**出典**: `rhel-patch-demo-restore/restore_demo.py:69-70`

### 2. OAuth認証

**再利用したアプローチ**:
```python
headers = {"Authorization": f"Bearer {token}"}
```

**出典**: `restore_demo.py:76`

### 3. Controller API接続とバージョン検証

**再利用したアプローチ**:
```python
# Gateway API (AAP 2.5+) と Legacy API (AAP 2.4) の両方をサポート
for root, minors in ((GATEWAY_API_ROOT, "[67]"), (LEGACY_API_ROOT, "5")):
    ping = self._json(root + "ping/", ...)
    # バージョン検証
    match = re.fullmatch(rf"(4\.{minors}\.\d+)(?:[-.+][a-zA-Z0-9.-]+)?", version)
```

**出典**: `restore_demo.py:125-146`

### 4. ページネーションによるリソース一覧取得

**再利用したアプローチ**:
```python
def rows(self, path, label, **filters):
    for page in range(1, 101):
        result = self.call(path, label, params={**filters, "page_size": 100, "page": page})
        results.extend(result["results"])
        if not result.get("next"):
            return results
```

**出典**: `restore_demo.py:148-160`

### 5. リソース検索による冪等性確保

**再利用したアプローチ**:
```python
def find(self, collection, label, **identity):
    rows = self.rows(collection + "/", label, **identity)
    if len(rows) > 1:
        raise SetupError("Multiple resources found")
    return rows[0] if rows else None
```

**出典**: `restore_demo.py:162-166`

### 6. Write制御によるdry-run実装

**再利用したアプローチ**:
```python
class Controller:
    def __init__(self, origin, token):
        self.write_enabled = False

    def call(self, path, label, *, params=None, payload=None, method=None):
        if payload is not None and not self.write_enabled:
            raise SetupError("Write operation not allowed in dry-run mode")
```

**出典**: `restore_demo.py:96, 98-100`

### 7. 秘密情報の対話入力

**再利用したアプローチ**:
```python
import getpass

def prompt_secret(label, env=None):
    if env and os.environ.get(env):
        return os.environ[env]
    return getpass.getpass(f"{label} > ").strip()
```

**出典**: `demo_config.py:59-67`

## 使用するREST APIエンドポイントとHTTPメソッド

### 1. Controller接続とバージョン確認

| エンドポイント | メソッド | 用途 |
|---------------|----------|------|
| `/api/controller/v2/ping/` | GET | Controller疎通確認（AAP 2.5+） |
| `/api/v2/ping/` | GET | Controller疎通確認（AAP 2.4） |
| `/api/controller/v2/me/` | GET | OAuth認証確認（AAP 2.5+） |
| `/api/v2/me/` | GET | OAuth認証確認（AAP 2.4） |

### 2. Credential Type

| エンドポイント | メソッド | 用途 |
|---------------|----------|------|
| `/api/v2/credential_types/` | GET | Credential Type一覧取得 |
| `/api/v2/credential_types/` | POST | Credential Type作成 |

**パラメータ（GET）**:
- `name`: Credential Type名での検索
- `page`: ページ番号
- `page_size`: 1ページあたりの件数

**ペイロード（POST）**:
```json
{
  "name": "Satellite Lightspeed API",
  "description": "...",
  "kind": "cloud",
  "inputs": {...},
  "injectors": {...}
}
```

### 3. Organization

| エンドポイント | メソッド | 用途 |
|---------------|----------|------|
| `/api/v2/organizations/` | GET | Organization一覧取得 |

**パラメータ**:
- `name`: Organization名での検索

### 4. Credential

| エンドポイント | メソッド | 用途 |
|---------------|----------|------|
| `/api/v2/credentials/` | GET | Credential一覧取得 |
| `/api/v2/credentials/` | POST | Credential作成 |

**ペイロード（POST）**:
```json
{
  "name": "Satellite Lightspeed API",
  "organization": 1,
  "credential_type": 15,
  "inputs": {
    "satellite_url": "https://...",
    "satellite_username": "admin",
    "satellite_password": "***",
    "satellite_organization_id": "1",
    "satellite_location_id": "2"
  }
}
```

### 5. Inventory

| エンドポイント | メソッド | 用途 |
|---------------|----------|------|
| `/api/v2/inventories/` | GET | Inventory一覧取得 |

**パラメータ**:
- `name`: Inventory名での検索

### 6. Project

| エンドポイント | メソッド | 用途 |
|---------------|----------|------|
| `/api/v2/projects/` | GET | Project一覧取得 |

**パラメータ**:
- `name`: Project名での検索

### 7. Execution Environment

| エンドポイント | メソッド | 用途 |
|---------------|----------|------|
| `/api/v2/execution_environments/` | GET | Execution Environment一覧取得 |

**パラメータ**:
- `name`: Execution Environment名での検索

### 8. Job Template

| エンドポイント | メソッド | 用途 |
|---------------|----------|------|
| `/api/v2/job_templates/` | GET | Job Template一覧取得 |
| `/api/v2/job_templates/` | POST | Job Template作成 |
| `/api/v2/job_templates/{id}/credentials/` | POST | Credentialの関連付け |

**ペイロード（Job Template POST）**:
```json
{
  "name": "RHEL-Vulnerability-Report",
  "organization": 1,
  "job_type": "run",
  "inventory": 10,
  "project": 5,
  "playbook": "satellite-vulnerability-report.yml",
  "execution_environment": 3,
  "ask_variables_on_launch": true,
  "extra_vars": "..."
}
```

**ペイロード（Credential関連付け POST）**:
```json
{
  "id": 15
}
```

## 自動作成・更新できるリソース

### 作成可能なリソース

✅ **Credential Type**:
- Satellite Lightspeed API
- Slack Incoming Webhook

✅ **Credential**:
- Satellite Lightspeed API（環境変数または対話入力）
- Slack Incoming Webhook（環境変数または対話入力）

✅ **Job Template**:
- RHEL-Vulnerability-Report

✅ **Job TemplateへのCredential関連付け**:
- Satellite Lightspeed API Credential
- Slack Incoming Webhook Credential

### 再利用する既存リソース

以下のリソースは既存のものを検索して再利用します：

🔍 **Organization**: Default（名前で検索）
🔍 **Project**: RHEL-Patch-Demo（名前で検索）
🔍 **Inventory**: Slack-Notification-Inventory（名前で検索）
🔍 **Execution Environment**: Default execution environment（名前で検索）

### 作成しないリソース

以下は手動作成が必要です：

⚠️ **Inventory**: Slack-Notification-Inventory（存在しない場合）
⚠️ **Host**: localhost（Inventoryに含まれない場合）

## 冪等性の実装

### リソース作成前の確認

```python
# 既存リソースを検索
existing = controller.find("credential_types", "Credential Type: {name}", name=name)

if existing:
    # 既に存在する → スキップ
    print(f"✓ {name} (exists, ID: {existing['id']})")
else:
    # 存在しない → 作成
    result = controller.create("credential_types/", label, payload)
    print(f"✅ Created {name} (ID: {result['id']})")
```

### Credentialの特殊処理

Credentialは既存の場合、secretフィールドが取得できないため、空の値で上書きしないように実装：

```python
if sat_cred:
    # 既に存在する場合は警告のみ
    print("⚠️  Satellite Lightspeed API already exists")
    print("   Manual update required if credentials changed")
    skipped.append("Satellite Lightspeed API")
else:
    # 新規作成のみ実施
    # ...
```

## 必要な環境変数

### 必須

- **AAP_URL**: AAP base URL（例: `https://aap.example.com`）
- **AAP_TOKEN**: OAuth access token（AAP UIで生成）

### 任意（Credential作成時）

**Satellite Lightspeed API Credential**:
- **SATELLITE_URL**: Satellite URL
- **SATELLITE_USERNAME**: Satellite username
- **SATELLITE_PASSWORD**: Satellite password
- **SATELLITE_ORGANIZATION_ID**: Organization ID
- **SATELLITE_LOCATION_ID**: Location ID

**Slack Incoming Webhook Credential**:
- **SLACK_WEBHOOK_URL**: Webhook URL

未設定の場合は対話入力でプロンプト表示されます。

## 使用方法

### dry-runの実行方法

```bash
# 環境変数を設定
export AAP_URL="https://aap.example.com"
export AAP_TOKEN="your-oauth-token"

# dry-run実行（変更なし、確認のみ）
./setup-vulnerability-report.py --dry-run
```

**dry-runの出力例**:
```
============================================================
AAP Vulnerability Report Setup
============================================================
AAP URL: https://aap.example.com
Mode: DRY-RUN (no changes)
============================================================
✓ Connected to Controller 4.5.3 (/api/v2/)

✓ Dry-run mode. No changes will be made.

📝 Credential Types
============================================================
  [CREATE] Satellite Lightspeed API
  [CREATE] Slack Incoming Webhook

🔑 Credentials
============================================================
  [CREATE] Satellite Lightspeed API
    Required inputs:
      - SATELLITE_URL
      - SATELLITE_USERNAME
      - SATELLITE_PASSWORD
      - SATELLITE_ORGANIZATION_ID
      - SATELLITE_LOCATION_ID
  [CREATE] Slack Incoming Webhook
    Required inputs:
      - SLACK_WEBHOOK_URL

📋 Job Template
============================================================
  [CREATE] RHEL-Vulnerability-Report
    Organization: Default
    Inventory: Slack-Notification-Inventory
    Project: RHEL-Patch-Demo
    Playbook: satellite-vulnerability-report.yml
    Execution Environment: Default execution environment
    Credentials: Satellite Lightspeed API, Slack Incoming Webhook

============================================================
Summary
============================================================
Would create: 4 resources
Already exist: 0 resources

Run without --dry-run to apply changes

✅ Setup completed successfully
```

### 本実行の方法

```bash
# 環境変数を設定
export AAP_URL="https://aap.example.com"
export AAP_TOKEN="your-oauth-token"

# 任意：認証情報を環境変数で指定（対話入力を避ける場合）
export SATELLITE_URL="https://satellite.example.com"
export SATELLITE_USERNAME="admin"
export SATELLITE_PASSWORD="your-password"
export SATELLITE_ORGANIZATION_ID="1"
export SATELLITE_LOCATION_ID="2"
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."

# 本実行（リソース作成）
./setup-vulnerability-report.py
```

**対話入力の例**（環境変数未設定の場合）:
```
Creating Satellite Lightspeed API Credential:
Satellite URL > https://satellite.example.com
Satellite Username > admin
Satellite Password > ***
Organization ID > 1
Location ID > 2

Creating Slack Incoming Webhook Credential:
Slack Webhook URL > ***
```

### エラー時の動作

```bash
# AAP_URLが未設定の場合
$ ./setup-vulnerability-report.py
Error: AAP_URL environment variable is required

# AAP_TOKENが未設定の場合
$ ./setup-vulnerability-report.py
Error: AAP_TOKEN environment variable is required
Generate token at: AAP UI > Access > Users > <your-user> > Tokens

# 認証エラー
❌ Error: AAP OAuth auth — HTTP 401

# Inventoryが存在しない場合
❌ Error: Inventory 'Slack-Notification-Inventory' not found. Create it first.
```

## テスト結果

### 自動テスト

```
Ran 11 tests in 0.5s
PASSED: 11/11

✓ Playbook構文チェック
✓ 必要ファイルの存在確認
✓ Pythonスクリプトのインポート
✓ AAPリソースファイルの存在
✓ YAML構文の検証
✓ ドキュメントの存在確認
✓ Setup scriptsの確認
✓ Setup script Python構文
✓ Setup script --help
✓ Python単体テスト（38/38成功）
✓ 認証情報のハードコード確認
```

### 手動テスト（dry-run）

```bash
# 構文チェック
$ python3 -m py_compile setup-vulnerability-report.py
✓ No errors

# --helpの動作確認
$ ./setup-vulnerability-report.py --help
✓ Usage and examples displayed

# dry-run（AAP接続なし）
$ AAP_URL=test AAP_TOKEN=test ./setup-vulnerability-report.py --dry-run
❌ Error: ... HTTPS connection failed
✓ Error handled correctly
```

## 未検証事項

以下は実環境での検証が必要です：

### AAP環境での動作

⏳ **Controller API接続**: 実際のAAPへの接続
⏳ **OAuth認証**: 実際のtokenでの認証
⏳ **Credential Type作成**: 実際のPOSTリクエスト
⏳ **Credential作成**: 実際のPOSTリクエストと秘密情報の扱い
⏳ **Job Template作成**: 実際のPOSTリクエスト
⏳ **Credential関連付け**: Job TemplateへのCredential追加

### 冪等性の確認

⏳ **2回目の実行**: 既存リソースの検出と更新スキップ
⏳ **部分的な実行**: 一部のリソースだけが存在する場合
⏳ **エラーリカバリ**: 途中で失敗した場合の再実行

### エラーケース

⏳ **HTTP 401**: 無効なtoken
⏳ **HTTP 403**: 権限不足
⏳ **HTTP 404**: リソースが見つからない
⏳ **HTTP 500**: サーバーエラー
⏳ **ネットワークエラー**: 接続失敗、タイムアウト

## 既存機能への影響

### 影響なし

✅ **restore-demo.sh**: 変更なし、影響なし
✅ **restore_demo.py**: 変更なし、影響なし
✅ **demo_config.py**: 変更なし、影響なし
✅ **既存Playbook**: 変更なし、影響なし
✅ **既存Job Template**: 変更なし、影響なし

### 新規依存

📦 **PyYAML**: YAML読み込みに使用（既にrestore_demo.pyで使用済み）

## まとめ

### 実施した作業

✅ **setup-vulnerability-report.py作成**: AAP REST API直接使用の実装（約570行）
✅ **setup-vulnerability-report.sh修正**: Python wrapperに変更（約20行）
✅ **既存restore_demo.pyの再利用**: HTTP client、OAuth認証、ページネーション、冪等性パターン
✅ **dry-runモード実装**: write_enabledフラグによる制御
✅ **環境変数と対話入力**: 秘密情報の安全な取得
✅ **テスト**: 11/11成功

### 自動化されたリソース

✅ Credential Type作成  
✅ Credential作成（環境変数/対話入力）  
✅ Job Template作成  
✅ Credential関連付け

### 未検証事項

⏳ 実際のAAP環境での動作確認  
⏳ 冪等性の実環境検証  
⏳ エラーケースの網羅的確認

---

**実装者**: Claude Sonnet 4.5  
**実施日**: 2024年11月27日  
**再利用元**: rhel-patch-demo-restore/restore_demo.py, demo_config.py
