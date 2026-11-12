import os
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

app = App(token=os.environ["SLACK_BOT_TOKEN"])

@app.action("approve_patch")
def handle_approval(ack, body, respond):
    ack()

    user_id = body["user"]["id"]
    rhsa = body["actions"][0]["value"]

    print(f"Approved by {user_id}: {rhsa}")

    respond(
        text=f"承認操作を受信しました: {rhsa}\n"
             "※テスト中のためパッチ適用は実行しません。",
        response_type="ephemeral"
    )

@app.action("reject_patch")
def handle_rejection(ack, body, respond):
    ack()

    print(f"Rejected by {body['user']['id']}")

    respond(
        text="保留操作を受信しました。パッチ適用は行いません。",
        response_type="ephemeral"
    )

if __name__ == "__main__":
    print("Waiting for Slack interactions...")
    SocketModeHandler(
        app,
        os.environ["SLACK_APP_TOKEN"]
    ).start()

