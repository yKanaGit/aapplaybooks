# RHEL Patch Demo – AAP restoration

This restores **AAP Controller settings**, not the AAP installation, Satellite server, RHEL images, SSH private keys, or EDA.

It creates:
- `Slack-Notification-Inventory` and `localhost`
- `Slack Incoming Webhook` custom credential type
- `Slack-Patch-Demo` credential **only when** `SLACK_WEBHOOK_URL` is supplied
- `RHEL-Patch-Demo` Git project and sync
- `RHEL-Patch-Check`, `RHEL-Patch-Apply` (safe: `apply_patch: false`), `RHEL-Slack-Notification` job templates
- `RHEL-Patch-Workflow` with Check → AAP Approval → Apply

It does **not** create a Schedule, launch jobs, register RHEL to HCC, or set up EDA.

## Before rerunning in a new sandbox

1. Have a working AAP Controller (and its API URL).
2. Prepare the existing `Workshop Inventory` and SSH Machine Credential in the new sandbox. They may have different names. Verify `node1.example.com` resolves and is accessible from the AAP execution environment.
3. Ensure the Git repository contains `rhel_patch.yml` and `slack_notify.yml` and the account can fetch it. If it is private, set up a Source Control Credential beforehand.
4. Edit `aap-config/demo-vars.yml` (SCM URL, credential name, inventory, target host, EE). **Never put secrets in this file.**
5. Check that the existing selected Execution Environment supports your RHEL 7.9 managed node; alternatively use a supported RHEL host. On a new Satellite, the example RHSA may be unavailable or already installed.

## On Mac – run with a dedicated virtual environment

```zsh
python3 -m venv ~/aap-restore-venv
source ~/aap-restore-venv/bin/activate
python -m pip install 'ansible-core>=2.16,<2.18' requests
ansible-galaxy collection install -r requirements.yml

export CONTROLLER_HOST='https://YOUR-AAP-CONTROLLER-URL'
export CONTROLLER_USERNAME='admin'
read -rs 'CONTROLLER_PASSWORD?AAP Password: '; echo
export CONTROLLER_PASSWORD
read -rs 'SLACK_WEBHOOK_URL?Slack Webhook URL: '; echo
export SLACK_WEBHOOK_URL

ansible-playbook -i localhost, restore_aap.yml
```

`CONTROLLER_HOST` must be the URL of **Automation Controller**, not the EDA URL. API paths and credentials differ across AAP versions/deployments. If the environment uses self-signed certs, install/trust the CA rather than disabling TLS validation.

If you prefer to enter `Slack-Patch-Demo` manually, omit `SLACK_WEBHOOK_URL`, create that credential in AAP, and set `demo_slack_credential_already_exists: true`.

## After restore

1. Verify Project Sync is successful; templates point to the correct Playbooks.
2. Test `RHEL-Patch-Check` (read-only) and `RHEL-Slack-Notification` first.
3. Check Workflow Visualizer nodes and approval timeout. This flow still needs AAP approval; it is **not** a Slack-only approval flow.
4. `RHEL-Patch-Apply` is intentionally configured with `apply_patch: false` after restoration. Only enable actual update after confirming the demo host can be changed, a backup/recovery path exists, and the target RHSA is valid and installable.

## Scope and caveats

- Automation uses the `awx.awx` 24.6.1 modules (Controller API). Validate compatibility with your AAP version before use. Red Hat supports `ansible.controller` in supported AAP installations; this sample uses community modules for ease of setup.
- This playbook does not recreate the existing SSH credential or source-control token.
- `RHEL-Patch-Apply` was originally `apply_patch: true`; it is deliberately restored as `false` to avoid accidental execution.
- The underlying `rhel_patch.yml` example is not a complete hardened patch automation workflow.
- Do not push Slack tokens, SSH keys, AAP passwords, or Webhook URLs to GitHub.
