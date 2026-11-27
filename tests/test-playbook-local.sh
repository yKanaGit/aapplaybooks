#!/bin/bash
#
# Local playbook test with mock environment
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"

echo "Testing Playbook with mock environment..."

# Set mock environment variables
export SATELLITE_URL="https://satellite.example.com"
export SATELLITE_USERNAME="admin"
export SATELLITE_PASSWORD="test-password"
export SATELLITE_ORGANIZATION_ID="1"
export SATELLITE_LOCATION_ID="2"
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/TEST/TEST/TEST"

# Create test JSON file for the case where satellite_vulnerability.py would fail
mkdir -p /tmp/test-vulnerability-report
cat > /tmp/test-vulnerability-report/test-report.json <<'EOF'
{
  "schema_version": 1,
  "generated_at": "2024-11-27T12:00:00+00:00",
  "source": "satellite",
  "mode": "cve",
  "requested_cve": "CVE-2024-TEST",
  "requested_top": null,
  "complete": true,
  "cves": [{
    "cve_id": "CVE-2024-TEST",
    "description": "Test vulnerability",
    "impact": "Important",
    "cvss3_score": "7.5",
    "known_exploit": false,
    "public_date": "2024-01-01T00:00:00+00:00",
    "cve_rhsa_ids": null,
    "affected_host_counts": {
      "before_advisory_filter": 1,
      "after_advisory_filter": 1,
      "fetched_after_advisory_filter": 1
    },
    "affected_hosts": [{
      "display_name": "test.example.com",
      "os": "RHEL 7.9",
      "rhsa_ids": ["RHSA-2024:0001"],
      "first_reported": "2024-01-01T12:00:00+00:00",
      "last_evaluation": "2024-01-01T13:00:00+00:00"
    }]
  }]
}
EOF

echo "✓ Mock environment configured"
echo "✓ Test JSON created"

# Note: Actual playbook execution requires AAP environment
# This test only verifies the setup
echo ""
echo "Test setup completed successfully."
echo "Playbook can now be tested in AAP environment."

# Cleanup
rm -rf /tmp/test-vulnerability-report

exit 0
