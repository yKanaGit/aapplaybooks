# Lightspeed on Satellite 脆弱性情報取得スクリプト - 実装完了報告

## 実装日
2024年11月27日

## 作成・変更したファイル

### 1. メインスクリプト
- **satellite_vulnerability.py** (新規作成)
  - Lightspeed on Satellite APIからの脆弱性情報取得
  - HCC版互換のJSON出力
  - 行数: 約800行

### 2. テストファイル
- **tests/test_satellite_vulnerability.py** (新規作成)
  - 単体テスト（15テストケース）
  - モックを使用したAPI呼び出しテスト
  - 行数: 約250行

- **tests/test_slack_compatibility.py** (新規作成)
  - Slack通知互換性テスト（4テストケース）
  - slack_notify.pyとの統合テスト
  - 行数: 約200行

### 3. ドキュメント
- **SATELLITE_README.md** (新規作成)
  - 日本語による完全なドキュメント
  - 使用方法、API仕様、エラーハンドリング、既知の制約
  - 行数: 約600行

- **IMPLEMENTATION_REPORT.md** (このファイル)
  - 実装完了報告

## 実装した機能

### コア機能

1. **Satellite API クライアント (`SatelliteClient`)**
   - Basic認証
   - GETリクエストにJSON Bodyを送信（Satellite特有の仕様）
   - SSL/TLS証明書検証（オプションで無効化可能）
   - 自動リトライ（HTTP 429, 5xx）
   - タイムアウト設定（20秒）

2. **CVE情報取得**
   - 特定CVEの取得（`--cve`）
   - 上位N件の取得（`--top`）
   - CVE一覧のみ取得（`--list`）
   - 重要度による絞り込み（`--impact`）
   - CVSSスコアによる絞り込み（`--cvss-from`, `--cvss-to`）
   - Known Exploitによる絞り込み（`--known-exploit`）

3. **環境フィルタリング**
   - Content Viewによる絞り込み（`--content-view`）
   - Lifecycle Environmentによる絞り込み（`--lifecycle-environment`）
   - ホスト情報のtagsフィールド解析

4. **ページネーション**
   - offset/limitベースのページング
   - 循環検知
   - total_items一致確認
   - 最大10,000ページまで追跡

5. **エラーハンドリング**
   - HTTP 401/403: 認証エラー（即座に終了）
   - HTTP 429/5xx: リトライ（指数バックオフ）
   - HTTP 400: パラメーターエラー（詳細メッセージ）
   - タイムアウト、ネットワークエラー
   - 部分取得時のcompleteフラグ設定

6. **セキュリティ**
   - パスワードの非表示入力（getpass）
   - 環境変数からの認証情報読み込み
   - Basic認証ヘッダーの適切な生成
   - パスワードをログに出力しない

7. **JSON出力**
   - HCC版互換のスキーマ
   - `source: "satellite"`で識別
   - Content View/Lifecycle Environment情報を含む
   - 不完全なデータの明示（`complete: false`）
   - エラー詳細の記録

## 実行方法

### 基本的な実行

```bash
# 環境変数で設定
export SATELLITE_URL="https://satellite.example.com"
export SATELLITE_USERNAME="admin"
export SATELLITE_PASSWORD="password"
export SATELLITE_ORGANIZATION_ID="1"
export SATELLITE_LOCATION_ID="2"

# 特定CVEを取得
python3 satellite_vulnerability.py --cve CVE-2024-2961 --insecure

# 上位10件を取得
python3 satellite_vulnerability.py --top 10 --insecure

# Content Viewで絞り込み
python3 satellite_vulnerability.py \
  --top 5 \
  --content-view RHEL7 \
  --lifecycle-environment RHEL7_Dev \
  --insecure
```

### 対話入力

環境変数が未設定の場合、対話入力で実行可能：

```bash
$ python3 satellite_vulnerability.py --cve CVE-2024-2961 --insecure
Satellite URL: https://satellite.example.com
Satellite Username: admin
Satellite Password: [非表示入力]
Organization ID [1]: 
Location ID [2]: 
CVE 一覧を取得中...
...
```

## テスト結果

### 単体テスト: すべて成功 (15/15)

```
test_count ... ok
test_impact_rank ... ok
test_obj ... ok
test_parse_tags ... ok
test_rank_key ... ok
test_rhsa_ids ... ok
test_client_initialization ... ok
test_get_json_http_401 ... ok
test_get_json_success ... ok
test_fetch_empty_response ... ok
test_fetch_multiple_pages ... ok
test_fetch_single_page ... ok
test_fetch_with_api_error ... ok
test_auth_header_format ... ok
test_client_does_not_expose_password ... ok

Ran 15 tests in 0.007s
OK
```

### Slack互換性テスト: すべて成功 (4/4)

```
test_incomplete_report_handling ... ok
test_json_schema_compatibility ... ok
test_slack_notify_can_load_satellite_json ... ok
test_top_mode_rejection ... ok

Ran 4 tests in 0.002s
OK
```

### 構文チェック: 成功

```bash
python3 -m py_compile satellite_vulnerability.py
✓ 構文チェック成功
```

### ヘルプ表示: 正常動作

```bash
./satellite_vulnerability.py --help
# 日本語のヘルプが正しく表示される
```

## 未検証の項目（実環境でのテストが必要）

以下の項目は、実際のSatellite環境でのテストが必要です：

### 1. 実際のAPI接続

**必要な情報**:
- Satellite URL
- 管理者ユーザー名とパスワード
- Organization ID
- Location ID

**テスト内容**:
- HTTP 200レスポンスの確認
- JSON構造の検証
- CVE情報の取得成功

### 2. CVE-2024-2961の取得

**コマンド**:
```bash
python3 satellite_vulnerability.py \
  --cve CVE-2024-2961 \
  --insecure \
  --output test_cve_2961.json
```

**確認項目**:
- CVE詳細情報が取得できるか
- 影響ホスト（node1.example.com、node2.example.com）が取得できるか
- RHSA-2024:3588が含まれているか

### 3. 影響ホストの取得

**確認項目**:
- display_name
- inventory_id
- insights_id
- os
- advisories_list
- tags（Content View、Lifecycle Environment）
- first_reported、last_evaluation、last_upload

### 4. ページネーション

**テスト方法**:
```bash
# 全CVEを取得
python3 satellite_vulnerability.py \
  --list \
  --insecure \
  --output all_cves.json

# 件数を確認
cat all_cves.json | jq '.cve_list.fetched'
```

**確認項目**:
- 20件以上のCVEがある場合、複数ページが取得できるか
- total_itemsと実際の取得件数が一致するか

### 5. Content ViewとLifecycle Environmentによる絞り込み

**テスト方法**:
```bash
python3 satellite_vulnerability.py \
  --top 5 \
  --content-view RHEL7 \
  --lifecycle-environment RHEL7_Dev \
  --insecure \
  --output filtered.json

# フィルター後の件数を確認
cat filtered.json | jq '.cves[0].affected_host_counts'
```

**確認項目**:
- before_advisory_filter: CVE全体の影響ホスト数
- after_advisory_filter: アドバイザリありのホスト数
- content_view_filter: 環境絞り込み後の件数

### 6. エンドツーエンドのSlack通知

**テスト方法**:
```bash
# CVE取得
python3 satellite_vulnerability.py \
  --cve CVE-2024-2961 \
  --insecure

# Slack通知（ドライラン）
python3 slack_notify.py \
  --input output/vulnerability_report.json \
  --dry-run
```

**確認項目**:
- slack_notify.pyがSatellite版JSONを正しく読み込めるか
- Slack Block Kit JSONが正しく生成されるか
- CVE情報、影響ホスト、RHSAが表示されるか

## 実環境での検証手順

### ステップ1: 環境変数の設定

```bash
# 認証情報を設定
export SATELLITE_URL="https://satellite.zswk7.sandbox1268.opentlc.com"
export SATELLITE_USERNAME="admin"
export SATELLITE_PASSWORD="your-password"
export SATELLITE_ORGANIZATION_ID="1"
export SATELLITE_LOCATION_ID="2"
```

### ステップ2: CVE-2024-2961の取得

```bash
# 取得実行
python3 satellite_vulnerability.py \
  --cve CVE-2024-2961 \
  --insecure \
  --output test_cve_2961.json

# 結果確認
cat test_cve_2961.json | jq '.'

# CVE情報確認
cat test_cve_2961.json | jq '.cves[0] | {
  cve_id,
  impact,
  cvss3_score,
  description: (.description[:100]),
  rhsa_ids: .cve_rhsa_ids
}'

# 影響ホスト確認
cat test_cve_2961.json | jq '.cves[0].affected_hosts[] | {
  display_name,
  os,
  rhsa_ids,
  first_reported
}'
```

### ステップ3: 環境フィルタリングのテスト

```bash
# Content ViewとLifecycle Environmentで絞り込み
python3 satellite_vulnerability.py \
  --cve CVE-2024-2961 \
  --content-view RHEL7 \
  --lifecycle-environment RHEL7_Dev \
  --insecure \
  --output test_filtered.json

# フィルター結果確認
cat test_filtered.json | jq '.cves[0].affected_host_counts'
cat test_filtered.json | jq '.cves[0].affected_hosts | length'
```

### ステップ4: Slack通知テスト

```bash
# Slack Block Kit生成（ドライラン）
python3 slack_notify.py \
  --input test_cve_2961.json \
  --dry-run
```

### ステップ5: 全CVE一覧の取得

```bash
# 一覧取得
python3 satellite_vulnerability.py \
  --list \
  --insecure \
  --output all_cves.json

# 件数確認
cat all_cves.json | jq '.cve_list'
```

## 既知の未解決事項

### 1. 個別CVE詳細APIの使用不可

**現象**: `GET /cves/{cve_id}` エンドポイントがHTTP 404を返す

**対処**: CVE一覧APIからの情報で代替

**影響**: 軽微（一覧APIで主要な情報は取得可能）

### 2. tagsフィールドの構造が環境依存の可能性

**現象**: 実環境で確認した構造と異なる可能性

**対処**: 実環境での検証が必要

**影響**: Content ViewとLifecycle Environmentの絞り込みに影響

### 3. Technology Preview機能

**制約**: 
- Satellite 6.18のInsights on-premisesはTechnology Preview
- 本番環境での使用は推奨されない
- APIの仕様が変更される可能性あり

**対処**: Red Hatの正式リリースを待つ

## 次のステップ

1. **実環境での検証**
   - 認証情報を提供してCVE-2024-2961の取得テスト
   - 影響ホストとRHSA情報の確認
   - Content ViewとLifecycle Environmentによる絞り込みテスト

2. **エラーケースの確認**
   - HTTP 401/403エラーの再現と確認
   - ネットワークタイムアウトの動作確認
   - 不完全データ（complete: false）の処理確認

3. **Slack通知の完全テスト**
   - 実際のSlack Webhookへの送信
   - 通知フォーマットの確認

4. **AAPへの組み込み**
   - Credential Typeの作成
   - Job Templateの作成
   - 自動実行のテスト

5. **パフォーマンス測定**
   - 大量CVE（100件以上）の取得時間
   - ページネーション性能
   - API呼び出し回数の最適化

## まとめ

Lightspeed on Satellite版の脆弱性情報取得スクリプトを実装しました。

**実装完了項目**:
- ✅ Satellite API クライアント（Basic認証、GETリクエストのJSON Body）
- ✅ CVE情報取得（特定CVE、上位N件、一覧）
- ✅ 影響ホスト取得とRHSA情報の抽出
- ✅ Content ViewとLifecycle Environmentによる絞り込み
- ✅ ページネーション処理
- ✅ エラーハンドリングとリトライ
- ✅ HCC版互換のJSON出力
- ✅ 単体テスト（15テストケース、すべて成功）
- ✅ Slack通知互換性テスト（4テストケース、すべて成功）
- ✅ 包括的なドキュメント（日本語）

**未検証項目**（実環境でのテストが必要）:
- ⏳ 実際のSatellite APIへの接続
- ⏳ CVE-2024-2961の取得
- ⏳ 影響ホストとRHSA情報の完全性
- ⏳ 環境フィルタリングの動作
- ⏳ ページネーションの実動作
- ⏳ エンドツーエンドのSlack通知

実環境でのテストには、ユーザーによる認証情報の提供が必要です。
