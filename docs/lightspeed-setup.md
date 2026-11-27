# Lightspeed on-premises 再構築の自動化

RHDP環境を再払い出しした際に、Lightspeed on-premisesのインストールと管理対象RHELのInsights Client設定を自動的に再現するツールです。

## 目次

1. [対象環境](#対象環境)
2. [前提条件](#前提条件)
3. [接続構成](#接続構成)
4. [実行方法](#実行方法)
5. [実行モード](#実行モード)
6. [設定方法](#設定方法)
7. [処理の流れ](#処理の流れ)
8. [出力とログ](#出力とログ)
9. [トラブルシューティング](#トラブルシューティング)
10. [既知の未解決事項](#既知の未解決事項)
11. [手動復旧手順](#手動復旧手順)
12. [参考資料](#参考資料)

## 対象環境

### ソフトウェアバージョン

- Red Hat Satellite: 6.18.3
- Satellite OS: RHEL 9.7
- 管理対象ホスト: RHEL 7.9 (node1.example.com)
- Automation Controller: 既存環境を利用

### 組織構成

- Organization: Default Organization
- Lifecycle Environment: RHEL7_Dev
- Content View: RHEL7

### 重要な前提

- node1はSatelliteにSubscription Managerで登録済み
- LightspeedのVulnerability機能はTechnology Preview
- RHDP環境の再払い出しにより、外部FQDN、プライベートIPアドレス、認証情報などが変わる

## 前提条件

### Satellite側

1. Satellite 6.18.3以上がインストール済み
2. Podman Network Backendがnetavark
3. registry.redhat.ioへの疎通可能
4. security.access.redhat.comへの疎通可能
5. satellite-installerに--enable-iopオプションが存在
6. Hammer CLIが利用可能

### node1側

1. SatelliteにSubscription Managerで登録済み
2. rhel-7-server-rpmsリポジトリが利用可能

### SSH接続

以下の接続が確立されていること：

- Mac → 踏み台端末（Bastion）: SSH接続可能
- 踏み台端末 → Satellite: パスワードなしSSH接続可能
- 踏み台端末 → node1: パスワードなしSSH接続可能
- Satellite/node1: パスワードなしsudo実行可能

## 接続構成

```
Mac → 踏み台端末（Bastion）→ Satellite / node1
```

### SSH接続の特徴

- 踏み台端末のSSH認証設定を利用
- SSH秘密鍵はMacにコピーしない
- SSH Agent Forwardingは使用しない
- 踏み台端末に追加ソフトウェアをインストールしない
- 既存のSSH設定を変更しない
- SSHホスト鍵検証は有効（accept-newで初回のみ自動受け入れ）
- BatchMode=yesで事前確認を実施

## 実行方法

### 初回セットアップ

1. 設定ファイルを準備（任意）

```bash
cp config/lightspeed.env.example lightspeed.env
# lightspeed.envを編集して環境に合わせて設定
```

2. ドライラン実行で動作確認

```bash
./setup-lightspeed.sh --dry-run
```

3. 本実行

```bash
./setup-lightspeed.sh
```

### 再払い出し後の再実行

RHDP環境を再払い出しした後は、以下の情報のみ更新して再実行：

- 踏み台端末のSSH接続情報（BASTION_HOST、BASTION_USER）
- 必要に応じて認証情報（対話入力）

```bash
# 設定ファイルを更新してから実行
./setup-lightspeed.sh
```

## 実行モード

### 1. ドライラン（--dry-run）

リモート環境への変更を行わず、実行内容を確認します。

```bash
./setup-lightspeed.sh --dry-run
```

**用途:**
- 初回実行前の動作確認
- 設定内容の検証
- 現在の環境状態の把握

### 2. 完全セットアップ（デフォルト）

Satelliteのセットアップとnode1のInsights Client設定の両方を実行します。

```bash
./setup-lightspeed.sh
```

**処理内容:**
- Satellite事前確認
- DNS設定修正
- Registry認証設定
- Lightspeed on-premisesインストール
- 脆弱性情報同期
- Satelliteホストパラメーター設定
- Insights Clientインストール・登録
- システム情報アップロード
- 検証とレポート生成

### 3. Satelliteのみ（--satellite-only）

Satelliteのセットアップのみを実行します。

```bash
./setup-lightspeed.sh --satellite-only
```

**用途:**
- Satellite側の設定のみを変更したい場合
- node1への接続ができない場合の段階的セットアップ

### 4. クライアントのみ（--client-only）

node1のInsights Client設定のみを実行します。

```bash
./setup-lightspeed.sh --client-only
```

**用途:**
- Satelliteのセットアップが完了済みの場合
- Insights Client設定のみをやり直したい場合

**前提条件:**
- Satelliteのhost_registration_insightsパラメーターがtrueに設定済み
- Lightspeed on-premisesがインストール済み

### 5. 検証のみ（--verify）

現在の設定状態を確認し、レポートを生成します。

```bash
./setup-lightspeed.sh --verify
```

**用途:**
- セットアップ後の状態確認
- 問題の診断
- 定期的な健全性チェック

## 設定方法

設定は以下の3つの方法で提供できます（優先順位順）：

### 1. 環境変数

```bash
export BASTION_USER="your-username"
export BASTION_HOST="bastion.example.com"
./setup-lightspeed.sh
```

### 2. 設定ファイル（lightspeed.env）

```bash
# lightspeed.envファイルを作成
BASTION_USER="your-username"
BASTION_HOST="bastion.example.com"
BASTION_PORT="22"
SATELLITE_USER="ec2-user"
SATELLITE_HOST="satellite"
NODE1_USER="ec2-user"
NODE1_HOST="node1"
NODE1_HOSTNAME="node1.example.com"
```

カスタム設定ファイルの使用：

```bash
./setup-lightspeed.sh --config /path/to/custom-config.env
```

### 3. 対話入力

設定ファイルや環境変数で指定されていない項目は、実行時に対話的に入力を求められます。

## 処理の流れ

### Phase 1: Satellite事前確認

1. Satelliteバージョン確認
2. OSバージョン確認
3. システムリソース確認（CPU、メモリー、ディスク）
4. FQDN取得
5. プライベートIPv4アドレス取得
6. Podmanネットワークバックエンド確認（netavarkであること）
7. IOPインストール状態確認
8. IOPコンテナ稼働状態確認
9. ネットワーク疎通確認（registry.redhat.io、security.access.redhat.com）
10. satellite-installerオプション確認
11. Hammer CLI利用可否確認

### Phase 2: Satellite設定

#### DNS設定修正

- FQDNと実際のNIC IPアドレスの不一致を検知
- 不一致がある場合、/etc/hostsにエントリーを追加
- 修正前にバックアップを作成
- 重複エントリーを作成しない
- 修正後に正引き・逆引きを確認

#### Registry認証

- 既存の認証ファイル（/etc/foreman/registry-auth.json）を確認
- 有効な認証がない場合は対話入力で設定
- テストイメージの取得で認証を検証
- 認証情報をログに記録しない

#### Lightspeed on-premisesインストール

- IOPの導入状態を確認
- 未導入または部分的な導入の場合のみインストール
- satellite-installer --enable-iopを実行
- インストール後にコンテナ稼働状態を確認
- systemctl --failedで失敗サービスを確認

### Phase 3: 脆弱性情報同期

1. CVEマッピング情報のダウンロード確認
2. VMaaSのリポジトリ取り込み完了を待機
3. VMaaS Web APIのキャッシュ更新確認
4. Vulnerability Engineの同期状態確認
5. 必要に応じてVulnerability Engineの同期サービスを実行
6. CVE取得件数、リポジトリ取得件数の確認

**タイムアウト設定:**
- VMaaSリポジトリ同期: 600秒
- Vulnerability Engine同期: 300秒

### Phase 4: Insights Client設定

#### Satelliteホストパラメーター設定

- Hammer CLIを使用してhost_registration_insightsをtrueに設定
- 設定済みの場合はスキップ
- Hammer CLIが使用できない場合は手動設定を案内

#### node1のInsights Client設定

1. RHEL version確認
2. Subscription Manager登録状態確認
3. insights-clientインストール（未インストールの場合）
4. insights-client登録状態確認
5. 未登録または登録先が異なる場合は再登録
6. システム情報のアップロード実行
7. 最終的な登録状態確認

**エラーハンドリング:**
- 403 Forbiddenエラー時はhost_registration_insightsの設定を確認
- Machine-id競合時は自動的にunregister→registerを実行

### Phase 5: 検証とレポート生成

以下の項目を検証：

1. Satellite基本情報
2. DNS設定
3. IOPインストール状態
4. 脆弱性情報同期状態
5. PostgreSQLエラーの有無
6. Insights Client登録状態

検証結果はJSON形式でlightspeed-report.jsonに保存されます。

## 出力とログ

### 標準出力

カラー付きで実行状況を表示：

- **[INFO]** (青): 一般的な情報
- **[✓]** (緑): 成功
- **[!]** (黄): 警告
- **[✗]** (赤): エラー
- **[→]** (シアン): 実行ステップ
- **[SKIP]** (黄): スキップ

### ログファイル

すべての実行内容はタイムスタンプ付きでログファイルに記録されます。

ファイル名: `lightspeed-YYYYMMDD_HHMMSS.log`

**セキュリティ:**
- パスワード、トークン、鍵などはサニタイズされて記録
- satellite-installer --full-helpの出力は記録しない（DBパスワード含むため）

### レポートファイル

検証結果はJSON形式で保存されます。

ファイル名: `lightspeed-report.json`

**構造:**

```json
{
  "timestamp": "2026-11-27T12:34:56Z",
  "satellite": {
    "version": "6.18",
    "os": "Red Hat Enterprise Linux 9.7",
    "fqdn": "satellite.example.com"
  },
  "dns_config": {
    "status": "ok",
    "fqdn": "satellite.example.com",
    "ip": "10.0.1.10"
  },
  "lightspeed": {
    "status": "installed",
    "container_count": 19,
    "failed_services": 0
  },
  "vulnerability_sync": {
    "status": "ok",
    "cve_count": 68393,
    "vmaas_repos": 12,
    "vuln_repos": 10,
    "os_metadata": 0
  },
  "postgresql_errors": {
    "status": "no_errors",
    "count": 0
  },
  "insights_client": {
    "status": "installed",
    "version": "3.1.9-1.el7_9",
    "registered": true
  },
  "summary": {
    "status": "success",
    "total_checks": 6,
    "passed_checks": 6,
    "failed_checks": 0,
    "warning_checks": 0
  }
}
```

## トラブルシューティング

### SSH接続エラー

**症状:** `Failed to connect to bastion host`

**確認事項:**
1. 踏み台端末のホスト名/IPアドレスが正しいか
2. SSHポートが正しいか（デフォルト: 22）
3. SSH鍵認証が設定されているか
4. ネットワーク疎通があるか

**対処:**
```bash
# 手動でSSH接続を確認
ssh -p 22 user@bastion-host
```

### Passwordless sudo エラー

**症状:** `Passwordless sudo is not available`

**確認事項:**
Satellite/node1でsudoersが設定されているか

**対処:**
```bash
# 踏み台端末から各ホストに接続して確認
ssh satellite
sudo -n true  # パスワード入力を求められなければOK
```

### DNS設定エラー

**症状:** `DNS mismatch detected`

**原因:**
SatelliteのFQDNが外部DNSではパブリックIPに解決されるが、NICにはプライベートIPが設定されている

**自動対処:**
スクリプトが自動的に/etc/hostsにエントリーを追加します。

**手動対処が必要な場合:**
```bash
# Satelliteにログインして確認
hostname -f  # FQDNを確認
hostname -I  # IPアドレスを確認

# /etc/hostsに以下の形式で追加
# <プライベートIP> <FQDN>
# 例: 10.0.1.10 satellite.example.com
```

### Registry認証エラー

**症状:** `Failed to authenticate with Red Hat Registry`

**確認事項:**
1. Red Hatアカウントの認証情報が正しいか
2. Registry Service Accountのトークンが有効か
3. registry.redhat.ioへの疎通があるか

**対処:**
```bash
# Satelliteで手動テスト
sudo podman login --authfile /etc/foreman/registry-auth.json registry.redhat.io
```

### Lightspeedインストールエラー

**症状:** `satellite-installer failed`

**確認事項:**
1. Podman Network Backendがnetavarkか
2. 必要なコンテナイメージが取得可能か
3. ディスク空き容量が十分か
4. メモリーが十分か

**対処:**
```bash
# Satelliteでログ確認
sudo journalctl -u satellite -n 100
sudo satellite-maintain service status
```

### 脆弱性情報同期の警告

**症状:** `No repositories found in VMaaS` または `Repository count mismatch`

**原因:**
初回同期時にVulnerability Engineの同期がVMaaSのリポジトリ取り込み完了前に実行された

**対処:**
スクリプトが自動的にVulnerability Engineの同期サービスを再実行します。

手動で実行する場合：
```bash
# Satelliteで実行
sudo systemctl start iop-service-vuln-vmaas-sync.service
sudo systemctl status iop-service-vuln-vmaas-sync.service
```

### Insights Client 403 Forbiddenエラー

**症状:** `Registration failed with 403 Forbidden`

**原因:**
Satelliteのhost_registration_insightsパラメーターがfalseまたは未設定

**対処:**
1. Satellite UI で確認・変更
   - Hosts → All Hosts → node1.example.com
   - Parameters タブ
   - host_registration_insights を true に設定

2. Hammer CLIで変更
```bash
hammer host set-parameter --host node1.example.com \
  --name host_registration_insights \
  --parameter-type boolean \
  --value true
```

3. 再度Insights Client登録を実行
```bash
sudo insights-client --unregister
sudo insights-client --register
```

### PostgreSQL Stack Depth Errorの警告

**症状:** `PostgreSQL errors detected`

**詳細:**
VMaaSのRHEL 8 AppStreamリポジトリ同期中に以下のエラーが発生することがあります：

```
psycopg2.errors.StatementTooComplex:
stack depth limit exceeded
max_stack_depth (currently 2048kB)
```

**影響:**
データの完全性が保証されない可能性があります。

**対応方針:**
- 現時点では対応保留（既知の未解決事項）
- スクリプトはエラーを検知して警告を表示
- PostgreSQLの設定は自動変更しない
- リポジトリ件数の差異を結果レポートに記録

**参考情報:**
Red Hat Satellite 6.18 Known Issues

## 既知の未解決事項

### 1. PostgreSQL Stack Depth Error

**現象:**
VMaaS AppStreamリポジトリ同期時にPostgreSQLエラーが発生

**詳細:**
トラブルシューティングセクションを参照

**今後の対応:**
- Red Hatのパッチ/アップデートを適用
- PostgreSQLのmax_stack_depthパラメーター調整（要検証）

### 2. リポジトリ件数の差異

**現象:**
- VMaaS: 12リポジトリ
- Vulnerability Engine: 10リポジトリ

**原因:**
同期処理のタイミングまたはフィルタリングによる差異

**影響:**
軽微（主要なRHEL 7リポジトリは同期済み）

**対応:**
結果レポートに記録し、必要に応じて手動確認

### 3. OSメタデータ件数が0件

**現象:**
Vulnerability Engineのmetadataテーブルに0件

**原因:**
現時点で不明（調査中）

**影響:**
現在のところ、基本的な脆弱性分析には影響なし

**対応:**
結果レポートに記録し、継続監視

## 手動復旧手順

自動化スクリプトが使用できない場合の手動手順：

### 1. DNS設定修正

```bash
# Satelliteにログイン
ssh satellite

# FQDNとIPを確認
FQDN=$(hostname -f)
IP=$(hostname -I | awk '{print $1}')

# /etc/hostsをバックアップ
sudo cp -p /etc/hosts /etc/hosts.backup.$(date +%Y%m%d_%H%M%S)

# エントリーを追加
echo "${IP} ${FQDN}" | sudo tee -a /etc/hosts

# 確認
getent hosts ${FQDN}
```

### 2. Registry認証

```bash
# Satelliteにログイン
ssh satellite

# ログイン
sudo podman login --authfile /etc/foreman/registry-auth.json registry.redhat.io

# テストイメージ取得
sudo podman pull --authfile /etc/foreman/registry-auth.json \
  registry.redhat.io/satellite/iop-vulnerability-engine-rhel9:6.18

# クリーンアップ
sudo podman rmi registry.redhat.io/satellite/iop-vulnerability-engine-rhel9:6.18
```

### 3. Lightspeedインストール

```bash
# Satelliteにログイン
ssh satellite

# インストール
sudo satellite-installer --enable-iop

# 確認
sudo podman ps | grep iop
systemctl --failed
```

### 4. 脆弱性情報同期

```bash
# Satelliteにログイン
ssh satellite

# CVE件数確認
sudo podman exec iop-service-vmaas-reposcan \
  psql -U vmaas_writer -d vmaas -tAc 'SELECT COUNT(*) FROM cve;'

# VMaaSリポジトリ確認
sudo podman exec iop-service-vmaas-reposcan \
  psql -U vmaas_writer -d vmaas -tAc 'SELECT COUNT(*) FROM repo;'

# Vulnerability Engine同期実行
sudo systemctl start iop-service-vuln-vmaas-sync.service

# 同期状態確認
sudo systemctl status iop-service-vuln-vmaas-sync.service

# Vulnerability Engineリポジトリ確認
sudo podman exec iop-service-vuln-manager \
  psql -U vuln_user -d vulnerability -tAc 'SELECT COUNT(*) FROM repository;'
```

### 5. Insights Client設定

```bash
# Satelliteでホストパラメーター設定
hammer host set-parameter --host node1.example.com \
  --name host_registration_insights \
  --parameter-type boolean \
  --value true

# node1にログイン
ssh node1

# RHEL version確認
cat /etc/redhat-release

# Subscription Manager確認
sudo subscription-manager identity
sudo subscription-manager config | grep -E 'hostname|baseurl'

# insights-clientインストール（未インストールの場合）
sudo yum install -y insights-client

# 登録状態確認
sudo insights-client --status

# 登録（必要な場合）
sudo insights-client --unregister
sudo insights-client --register

# システム情報アップロード
sudo insights-client

# 結果確認
sudo insights-client --status
```

## 参考資料

### Red Hat Satellite 6.18 ドキュメント

- [Installing Satellite Server](https://docs.redhat.com/en/documentation/red_hat_satellite/6.18/html/installing_satellite_server)
- [Configuring Satellite with Lightspeed](https://docs.redhat.com/en/documentation/red_hat_satellite/6.18/html/managing_hosts/configuring-lightspeed_managing-hosts)
- [Managing Hosts Guide](https://docs.redhat.com/en/documentation/red_hat_satellite/6.18/html/managing_hosts)
- [Known Issues - Satellite 6.18](https://docs.redhat.com/en/documentation/red_hat_satellite/6.18/html/release_notes/known_issues)

### Technology Preview

Lightspeed on-premisesのVulnerability機能はRed Hat Satellite 6.18でTechnology Previewです：

- 本番環境での使用は推奨されません
- サポートポリシーが異なります
- 機能が変更される可能性があります

詳細: [Technology Preview Features](https://docs.redhat.com/en/documentation/red_hat_satellite/6.18/html/release_notes/technology_preview_features)

### Red Hat Insights

- [Red Hat Insights Client Configuration](https://docs.redhat.com/en/documentation/red_hat_insights/)
- [Registering Systems to Red Hat Insights](https://docs.redhat.com/en/documentation/subscription_central/1-latest/html/getting_started_with_red_hat_insights/)

### RHDP (Red Hat Demo Platform)

- [RHDP Portal](https://demo.redhat.com/)

---

**更新履歴:**
- 2026-11-27: 初版作成
