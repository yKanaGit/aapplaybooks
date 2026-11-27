# RHEL 7 ELS Demo Environment Rebuild Guide

## Overview

This guide describes how to rebuild Satellite Lightspeed and RHEL 7 Extended Life Cycle Support (ELS) demo environment from a new RHDP deployment.

## Prerequisites

- Red Hat Satellite 6.18.3 or later
- RHEL 7.9 hosts (node1, node2, node3)
- SSH access to Satellite and nodes
- Python 3 with required modules (for satellite_vulnerability.py)
- Hammer CLI configured on Satellite
- jq (for JSON processing)

## Verified Environment

This script was developed based on the following environment:

- **Satellite**: 6.18.3
- **AAP**: 2.4
- **Hosts**: node1.example.com, node2.example.com, node3.example.com (RHEL 7.9)
- **Organization**: Default Organization
- **Lifecycle Environment**: RHEL7_Dev
- **Base Content View**: RHEL7
- **ELS Content View**: RHEL7-ELS

## Measured Results

**Before ELS** (Baseline):
- CVE count: 40
- Latest CVE public date: 2024-04-24

**After ELS** (With Grouper optimization disabled):
- CVE count: 323
- Latest CVE public date: 2026-11-08
- New 2026 CVEs: 283

*Note: These are reference values from actual testing. Future environments may show different numbers.*

## Configuration

Create `els-demo.env` from the example:

```bash
cp config/els-demo.env.example els-demo.env
vi els-demo.env
```

Required configuration:
- `SATELLITE_HOST`: Satellite hostname (default: satellite)
- `ORGANIZATION`: Satellite organization (default: Default Organization)
- `LIFECYCLE_ENV`: Lifecycle Environment (default: RHEL7_Dev)
- `BASE_CONTENT_VIEW`: Base Content View (default: RHEL7)
- `ELS_CONTENT_VIEW`: ELS Content View (default: RHEL7-ELS)
- `NODE3_HOST`: Node3 hostname (default: node3.example.com)

## Usage

### Full Rebuild

```bash
# Dry run to check what would be done
./rebuild-els-demo.sh --dry-run

# Full rebuild (baseline + ELS enablement)
./rebuild-els-demo.sh
```

### Step-by-Step Execution

#### Step 1: Baseline Setup

Setup initial state without ELS enabled on node3:

```bash
./rebuild-els-demo.sh --baseline
```

This performs:
1. ✅ Verify Satellite IoP installation and services (auto-install if not present)
2. ✅ Setup Insights Client on all nodes (node1, node2, node3)
3. ✅ Enable and synchronize ELS repository to Satellite
4. ✅ Create RHEL7-ELS Content View with base + ELS repositories
5. ✅ Publish and promote to RHEL7_Dev
6. ✅ Configure Grouper optimization disable (demo-specific, with warning)
7. ✅ Synchronize VMaaS (CVE map, reposcan with log confirmation, webapp reload, vulnerability service)
8. ✅ Trigger Insights re-upload from all nodes
9. ✅ Wait for vulnerability evaluation (minimum 5 minutes, checks Evaluator/Grouper logs)
10. ✅ Capture baseline CVE data (all CVEs, validates complete=true)

**Important**: At this point, RHEL7-ELS Content View exists and contains ELS repositories, but **node3 is NOT assigned to it**. node3 remains on base Content View (RHEL7) for baseline measurement. ELS repository is NOT enabled on any node.

#### Step 2: Enable ELS

Enable ELS on node3 and trigger re-evaluation:

```bash
./rebuild-els-demo.sh --enable-els
```

This performs (following verified successful sequence):
1. ✅ **Assign node3 to RHEL7-ELS Content View** (makes ELS repos available)
2. ✅ Refresh subscription on node3
3. ✅ Enable rhel-7-server-els-rpms repository on node3
4. ✅ Verify repository list and ELS security updates
5. ✅ **Trigger VMaaS synchronization** (CVE map → reposcan with log confirmation → webapp reload → vmaas-sync)
6. ✅ Verify Grouper optimization disable (DISABLE_OPTIMISATION=True)
7. ✅ Send Insights data from node3 to Satellite
8. ✅ Wait for vulnerability evaluation (checks Evaluator/Grouper logs, minimum 5 minutes)
9. ✅ Capture ELS CVE data (all CVEs, validates complete=true)
10. ✅ Compare with baseline

**Critical**: VMaaS synchronization MUST complete BEFORE sending Insights data. This ensures the vulnerability database is updated with ELS CVE information before evaluation begins.

#### Step 3: Rollback Grouper Settings (Optional)

After demo, remove Grouper optimization disable setting:

```bash
./rebuild-els-demo.sh --rollback-grouper
```

This removes the demo-specific configuration and restores normal Grouper behavior.

## Grouper Optimization Disable

**Important**: This is a demo-specific configuration to disable Grouper's optimization logic.

**Why needed**: Grouper normally skips re-evaluation when "system not changed". Content View changes are not recognized as system changes.

**How it works**: Creates a Quadlet drop-in configuration that sets `DISABLE_OPTIMISATION=True` environment variable for the Grouper container.

**Location**: `/etc/containers/systemd/iop-service-vuln-grouper.container.d/99-demo-disable-optimisation.conf`

**Rollback**: Use `--rollback-grouper` to remove the configuration and restore default behavior.

## Output Files

Baseline and ELS CVE data are saved to `output/` directory:

```
output/
├── baseline-cves-20261127_120000.json
└── els-cves-20261127_130000.json
```

These can be used for comparison and Slack notifications.

## Verification

After completion, verify:

1. **IoP Services**: All IoP services are active
   ```bash
   ssh satellite "sudo systemctl status iop-service-vuln-grouper"
   ```

2. **ELS Repository**: Synchronized and available
   ```bash
   ssh satellite "sudo hammer repository list --search 'label=rhel-7-server-els-rpms'"
   ```

3. **Content View**: RHEL7-ELS published and promoted
   ```bash
   ssh satellite "sudo hammer content-view info --name RHEL7-ELS"
   ```

4. **Node3 Assignment**: Using RHEL7-ELS Content View
   ```bash
   ssh satellite "sudo hammer host info --name node3.example.com | grep 'Content View'"
   ```

5. **ELS Repository on Node3**: Enabled and showing security updates
   ```bash
   ssh node3 "sudo yum repolist | grep els"
   ssh node3 "sudo yum updateinfo list updates security | grep RHSA-2026"
   ```

6. **Grouper Configuration**: DISABLE_OPTIMISATION=True set
   ```bash
   ssh satellite "sudo podman exec iop-service-vuln-grouper printenv DISABLE_OPTIMISATION"
   ```

7. **CVE Count**: Increased after ELS enablement
   ```bash
   cat output/els-cves-*.json | jq '.cve_list.total_items'
   ```

## Troubleshooting

### IoP Services Not Active

Check IoP installation:
```bash
ssh satellite "sudo satellite-installer --enable-iop"
```

### ELS Repository Sync Failed

Check repository status:
```bash
ssh satellite "sudo hammer repository info --id <repo-id>"
```

Manual sync:
```bash
ssh satellite "sudo hammer repository synchronize --id <repo-id>"
```

### VMaaS Sync Timeout

Check service logs:
```bash
ssh satellite "sudo journalctl -u iop-service-vmaas-reposcan.service -n 100"
ssh satellite "sudo journalctl -u iop-service-vuln-vmaas-sync.service -n 100"
```

### Grouper Not Re-evaluating

Verify configuration:
```bash
ssh satellite "sudo systemctl cat iop-service-vuln-grouper.service | grep DISABLE_OPTIMISATION"
ssh satellite "sudo podman exec iop-service-vuln-grouper printenv DISABLE_OPTIMISATION"
```

Restart Grouper:
```bash
ssh satellite "sudo systemctl restart iop-service-vuln-grouper.service"
```

### No 2026 CVEs Detected

Wait for evaluation to complete (may take 10-15 minutes after insights-client run):
```bash
ssh satellite "sudo journalctl -u iop-service-vuln-grouper.service -f"
ssh satellite "sudo journalctl -u iop-service-vuln-evaluator.service -f"
```

Re-send Insights data:
```bash
ssh node3 "sudo insights-client"
```

## Known Limitations

- Grouper optimization disable is demo-specific and not officially documented
- VMaaS sync timing may vary depending on repository size
- Evaluation timing depends on Grouper and Evaluator processing queues
- CVE counts are reference values and may differ in other environments

## Unverified Items (Requiring Real Environment Testing)

The following items require verification in a real RHDP environment:

### 1. Vulnerability Evaluation Completion Detection
- **Service names**: Script uses `iop-service-vuln-evaluator.service` but actual services may be:
  - `iop-service-vuln-evaluator-upload.service`
  - `iop-service-vuln-evaluator-recalc.service`
- **Host identification**: Scripts search for hostname but logs may use Inventory UUID
- **Completion criteria**: Need to verify what log messages indicate successful evaluation vs. skipped evaluation
- **Impact**: Evaluation wait may timeout or return prematurely

### 2. VMaaS Synchronization Completion
- **Time zone**: Script uses local time for journalctl --since, but Satellite uses UTC
- **Reposcan success**: Need to verify exact log format including OK/SKIPPED status
- **Webapp reload**: Need to confirm Webapp loads the newly generated data dump
- **Oneshot service**: Need to verify systemctl Result field for current execution vs. cached status
- **Impact**: May incorrectly detect completion based on old logs

### 3. Grouper Configuration State Detection
- **Dry-run timing**: Need to verify file existence check doesn't prevent actual configuration
- **Environment variable verification**: Container environment may not reflect systemd changes immediately
- **Rollback verification**: Need to confirm environment variable is actually removed
- **Impact**: Configuration may not be applied or verified correctly

### 4. Baseline vs. ELS Host Set Consistency
- **Host filtering**: Need to verify same hosts are evaluated in both baseline and ELS runs
- **Content View effects**: Need to confirm removing --content-view doesn't change evaluation scope
- **Impact**: Comparison may be between different host sets

### 5. SSH Command Execution
- **Quoting in heredoc**: Complex commands with quotes need real environment testing
- **Exit code propagation**: Need to verify SSH failures are properly detected
- **Output capture**: Need to confirm command output isn't corrupted by SSH messages
- **Impact**: Commands may fail silently or produce incorrect output

## Related Documentation

- [ELS Vulnerability Investigation Report](els-vulnerability-investigation.md) - Detailed verification results
- [Lightspeed Setup Guide](lightspeed-setup.md) - Initial Lightspeed installation
- [AAP Integration Guide](AAP_INTEGRATION_GUIDE.md) - AAP vulnerability notification

---

**Last Updated**: 2026-11-27  
**Tested Environment**: RHDP Satellite 6.18.3, Lightspeed on Satellite (Technology Preview)
