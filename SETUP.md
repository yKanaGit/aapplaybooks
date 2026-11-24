# Mac の初回準備

リポジトリーのルートを作業ディレクトリーにします。Mac の Bash と zsh、Git、Python 3.12 を用意します。復元と Slack Bolt は同じ仮想環境を使えます。

```zsh
python3.12 -m venv "$HOME/.venvs/aap-demo-restore"
source "$HOME/.venvs/aap-demo-restore/bin/activate"
python3 -m pip install 'ansible-core>=2.20,<2.21'
python3 -m pip install -r slack-bolt/requirements.txt
python3 -m pip check
python3 -c 'import yaml, slack_bolt, slack_sdk'
./restore-demo.sh --help
./run-demo.sh --help
./start-slack-demo.sh --help
```

`PyYAML` が `import yaml` を提供します。別の仮想環境から起動する場合、その環境でも依存関係をインストールします。追加の Ansible Collection は不要です。社内 CA が必要な場合は、公開ルートも含む信頼済み PEM バンドルを `SSL_CERT_FILE` で指定してください。

## Slack アプリ

1. 使う Workspace の Slack アプリで Interactivity と Socket Mode を有効にします。
2. Bot Token に `chat:write`、App Token に `connections:write` を付けます。Bot scope を追加した場合はアプリを Workspace に再インストールします。
3. Bot を通知先チャンネルへ招待し、Workspace とチャンネルの ID、許可ユーザーの Slack メンバー ID を記録します。 [Slack Socket Mode](https://docs.slack.dev/tools/bolt-python/concepts/socket-mode/) · [投稿権限](https://docs.slack.dev/reference/methods/chat.postMessage/)

## 常駐サービス用の設定

非秘密設定を `.demo-local/config.json` に保存できます。既存環境から引き継ぐ場合も、AAP URL、Organization、ホスト、GitHub URL、`slack_team_id`、`slack_channel_id`、`slack_allowed_user_ids` を確認してください。許可ユーザー ID はカンマ区切りです。AAP OAuth トークン、Slack Bot Token、Slack App Token は、サービス起動時に非表示入力するか、`AAP_DEMO_OAUTH_TOKEN`、`SLACK_BOT_TOKEN`、`SLACK_APP_TOKEN` として安全なシェル環境から渡します。

復元と Project 同期の後、Mac で `./run-demo.sh --serve` を一度起動します。「通知 Job の監視を開始しました」と表示されたら、AAP 管理画面から `RHEL-Slack-Notification` を実行します。同じ Slack App で従来の `start-slack-demo.sh` も使う場合は、サービスを切り替えて起動してください。そのセッション中は、同じ Job Template を実行するたびに新しいイベントが Slack に届きます。詳細は [復元と繰り返し実行の手順](RESTORE.md) を参照してください。

## 初回に確認するローカルテスト

```zsh
python3 -B -m unittest discover -s rhel-patch-demo-restore
python3 -B -m unittest discover -s slack-bolt
ansible-playbook -i localhost, rhel-patch-demo-restore/restore_aap.yml
```

最後の Playbook コマンドは既定の `validate` モードで、接続先を使わずに定義を検査します。新しい環境を準備できたら [復元手順](RESTORE.md) へ進みます。
