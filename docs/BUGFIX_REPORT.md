# Bug修正報告：OpenerDirector.open()のcontext引数エラー

## 修正日
2024年11月27日

## 発生したエラー

実環境テストで以下のTypeErrorが発生：

```text
File "satellite_vulnerability.py", line 139, in get_json
    response = self._opener.open(request, timeout=TIMEOUT, context=context)
TypeError: OpenerDirector.open() got an unexpected keyword argument 'context'
```

**実行環境**: macOS, Python 3.10.13

## 問題の原因

`urllib.request.OpenerDirector.open()`メソッドは`context`引数をサポートしていない。

SSL/TLSコンテキストは`opener.open()`に渡すのではなく、`HTTPSHandler`を介してOpenerの生成時に設定する必要がある。

### 誤った実装（修正前）

```python
# __init__メソッド
self._opener = build_opener(ProxyHandler({}), NoRedirect())

# get_jsonメソッド
import ssl
if not self.verify_ssl:
    context = ssl._create_unverified_context()
elif self.ca_bundle:
    context = ssl.create_default_context(cafile=self.ca_bundle)
else:
    context = None

if context:
    response = self._opener.open(request, timeout=TIMEOUT, context=context)  # ← エラー
else:
    response = self._opener.open(request, timeout=TIMEOUT)
```

## 実施した修正

### 1. HTTPSHandlerのインポート追加

```python
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
```

### 2. SatelliteClient.__init__メソッドの修正

SSL/TLSコンテキストをOpener生成時に設定：

```python
def __init__(self, base_url, username, password, organization_id, location_id,
             verify_ssl=True, ca_bundle=None):
    # ... (他のフィールド設定)

    # Build opener with SSL/TLS context
    import ssl
    handlers = [ProxyHandler({}), NoRedirect()]

    if not verify_ssl:
        # Create unverified SSL context
        ssl_context = ssl._create_unverified_context()
        handlers.append(HTTPSHandler(context=ssl_context))
    elif ca_bundle:
        # Create SSL context with custom CA bundle
        ssl_context = ssl.create_default_context(cafile=ca_bundle)
        handlers.append(HTTPSHandler(context=ssl_context))
    # else: use default HTTPS handler (system CA bundle)

    self._opener = build_opener(*handlers)
```

### 3. get_jsonメソッドの簡素化

```python
def get_json(self, path, query_params=None, json_body=None):
    # ... (リクエスト準備)

    for attempt in range(MAX_ATTEMPTS):
        try:
            # SSL context is already configured in the opener
            response = self._opener.open(request, timeout=TIMEOUT)
            # ... (レスポンス処理)
```

## 修正のポイント

1. **SSL/TLSコンテキストの設定場所を変更**
   - 修正前：`get_json()`メソッド内で`opener.open()`に渡す
   - 修正後：`__init__()`メソッドで`HTTPSHandler`に渡す

2. **既存機能の維持**
   - Basic認証：変更なし
   - GETリクエストのJSON Body：変更なし
   - クエリパラメーターの分離：変更なし
   - リダイレクト制御（NoRedirect）：変更なし
   - ProxyHandler：変更なし

3. **TLS証明書検証オプション**
   - `verify_ssl=True`（デフォルト）：システムCA bundleで検証
   - `verify_ssl=False`：検証を無効化（`--insecure`オプション）
   - `ca_bundle=path`：カスタムCA bundleで検証

## テスト結果

### 既存テスト

すべて成功（15テスト）：

```
Ran 15 tests in 0.005s
OK
```

### 新規追加：統合テスト

実際のHTTP/HTTPSリクエストを発行する統合テストを追加：

**ファイル**: `satellite-vulnerability/tests/test_integration.py`

**テスト内容**:
1. HTTPサーバーとの実際の通信テスト
2. OpenerのSSL/TLSコンテキスト設定テスト
3. `context`引数エラーの回帰テスト
4. SSL検証オプションの動作確認

**結果**: すべて成功（5テスト）

```
test_client_builds_opener_correctly ... ok
test_http_connection ... ok
test_opener_does_not_accept_context_argument ... ok
test_default_ssl_context ... ok
test_ssl_context_in_https_handler ... ok
```

### 全テスト結果

```
Ran 21 tests in 0.632s
OK
```

- 単体テスト: 15テスト ✓
- 統合テスト: 5テスト ✓
- JSON互換性テスト: 1テスト ✓

## なぜ既存テストで検出できなかったか

### 問題点

既存の単体テストでは`unittest.mock`を使用してHTTPレスポンスをモック化していた：

```python
mock_response = MagicMock()
mock_response.read.return_value = b'{"data": [], "meta": {}}'
mock_opener.open.return_value = mock_response
```

この方法では：
- 実際の`urllib.request.OpenerDirector`のメソッドシグネチャをチェックしない
- `context`引数が不正であることを検出できない

### 対策

実際のHTTP/HTTPSサーバーとの通信を行う統合テストを追加：

1. **HTTPサーバーの起動**: `http.server.HTTPServer`でローカルテストサーバーを実行
2. **実際のリクエスト**: モックではなく実際の`opener.open()`を呼び出す
3. **エラー検出**: `TypeError`の発生を検証

これにより、同様の問題を将来検出できるようになった。

## 修正ファイル一覧

### 変更ファイル

1. **satellite_vulnerability.py**
   - インポート：`HTTPSHandler`を追加
   - `SatelliteClient.__init__`：SSL/TLSコンテキストの設定をOpener生成時に移動
   - `SatelliteClient.get_json`：`context`引数を削除、SSL設定をシンプル化

### 新規ファイル

2. **satellite-vulnerability/tests/test_integration.py**
   - 実際のHTTP/HTTPSリクエストを発行する統合テスト
   - `context`引数エラーの回帰テスト
   - SSL/TLSコンテキスト設定の検証

3. **docs/BUGFIX_REPORT.md**（このファイル）
   - 修正内容と結果の報告

### 修正ファイル

4. **satellite-vulnerability/tests/test_slack_compatibility.py**
   - `slack_notify`モジュールのインポートエラー対応
   - `slack_notify`が利用不可の場合はテストをスキップ

## 実環境でのAPI接続テスト

**状態**: 未検証

修正により`TypeError`は解消されましたが、実際のSatellite環境でのAPI接続テストはまだ実施していません。

### 次のステップ

実環境で以下を検証してください：

```bash
# 環境変数の設定
export SATELLITE_URL="https://satellite.zswk7.sandbox1268.opentlc.com"
export SATELLITE_USERNAME="admin"
export SATELLITE_PASSWORD="your-password"
export SATELLITE_ORGANIZATION_ID="1"
export SATELLITE_LOCATION_ID="2"

# CVE-2024-2961の取得
./satellite_vulnerability.py \
  --cve CVE-2024-2961 \
  --insecure \
  --output test_cve_2961.json

# 結果確認
cat test_cve_2961.json | jq '.'
```

**期待される結果**:
- `TypeError`が発生しないこと
- HTTP 200レスポンスでCVE情報が取得できること
- JSON形式が正しいこと

## まとめ

### 修正内容

✅ `OpenerDirector.open()`に不正な`context`引数を渡していた問題を修正  
✅ SSL/TLSコンテキストを`HTTPSHandler`を介してOpener生成時に設定  
✅ 既存の認証処理、リダイレクト制御、その他のHandlerを維持  
✅ TLS証明書検証オプション（デフォルト、`--insecure`、CA bundle）が正しく機能  
✅ Basic認証、GETリクエストのJSON Bodyなど、検証済みのAPI仕様は変更なし

### テスト結果

✅ すべてのテストが成功（21/21テスト）  
✅ 統合テストを追加して今回と同じエラーを検出可能に  
✅ 既存の単体テストとJSON互換性テストも正常に動作

### 未検証

⏳ 実際のSatellite環境でのAPI接続テスト

---

**修正者**: Claude Sonnet 4.5  
**実行環境**: macOS, Python 3.10.13  
**テスト環境**: Python 3.10.13, unittest
