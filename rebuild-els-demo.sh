#!/bin/bash
# RHEL 7 ELS Demo Environment Rebuild Script
# Rebuilds Satellite Lightspeed and ELS environment from new RHDP deployment

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/els/common.sh
source "${SCRIPT_DIR}/scripts/els/common.sh"

# Default configuration
CONFIG_FILE="${SCRIPT_DIR}/els-demo.env"
CONFIG_EXAMPLE="${SCRIPT_DIR}/config/els-demo.env.example"

# Default values
SATELLITE_HOST="${SATELLITE_HOST:-satellite}"
SATELLITE_USER="${SATELLITE_USER:-ec2-user}"
NODE3_HOST="${NODE3_HOST:-node3.example.com}"
ORGANIZATION="${ORGANIZATION:-Default Organization}"
LIFECYCLE_ENV="${LIFECYCLE_ENV:-RHEL7_Dev}"
BASE_CONTENT_VIEW="${BASE_CONTENT_VIEW:-RHEL7}"
ELS_CONTENT_VIEW="${ELS_CONTENT_VIEW:-RHEL7-ELS}"

# Execution mode
EXECUTION_MODE="full"  # full, baseline, enable-els, rollback-grouper
DRY_RUN=false

# Log file
LOG_FILE="${SCRIPT_DIR}/els-demo-$(date +%Y%m%d_%H%M%S).log"

show_usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Rebuild RHEL 7 ELS demo environment for Satellite Lightspeed.

OPTIONS:
    --dry-run              Perform checks without making changes
    --baseline             Setup initial state (IoP, ELS repo, Content View)
    --enable-els           Enable ELS on node3 and trigger re-evaluation
    --rollback-grouper     Remove Grouper optimization disable setting
    --config FILE          Use specified configuration file
    --help                 Show this help message

EXAMPLES:
    # Dry run to check what would be done
    ./rebuild-els-demo.sh --dry-run

    # Setup baseline (without ELS enabled on node3)
    ./rebuild-els-demo.sh --baseline

    # Enable ELS and trigger re-evaluation
    ./rebuild-els-demo.sh --enable-els

    # Rollback Grouper settings
    ./rebuild-els-demo.sh --rollback-grouper

CONFIGURATION:
    Configuration can be provided via els-demo.env file or environment variables.

    Required:
    - SATELLITE_HOST: Satellite hostname (default: satellite)
    - ORGANIZATION: Satellite organization (default: Default Organization)

    Optional:
    - LIFECYCLE_ENV: Lifecycle Environment (default: RHEL7_Dev)
    - BASE_CONTENT_VIEW: Base Content View name (default: RHEL7)
    - ELS_CONTENT_VIEW: ELS Content View name (default: RHEL7-ELS)
    - NODE3_HOST: Node3 hostname (default: node3.example.com)

For detailed instructions, see: docs/els-demo-rebuild.md
EOF
}

parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dry-run)
                DRY_RUN=true
                export DRY_RUN
                shift
                ;;
            --baseline)
                EXECUTION_MODE="baseline"
                shift
                ;;
            --enable-els)
                EXECUTION_MODE="enable-els"
                shift
                ;;
            --rollback-grouper)
                EXECUTION_MODE="rollback-grouper"
                shift
                ;;
            --config)
                CONFIG_FILE="$2"
                shift 2
                ;;
            --help)
                show_usage
                exit 0
                ;;
            *)
                echo "Unknown option: $1" >&2
                show_usage >&2
                exit 1
                ;;
        esac
    done
}

load_or_prompt_config() {
    log_info "=== Configuration ==="

    if [[ -f "${CONFIG_FILE}" ]]; then
        if load_config "${CONFIG_FILE}"; then
            log_success "Configuration loaded from ${CONFIG_FILE}"
        fi
    else
        log_info "Configuration file not found: ${CONFIG_FILE}"
        if [[ -f "${CONFIG_EXAMPLE}" ]]; then
            log_info "Example configuration available at: ${CONFIG_EXAMPLE}"
        fi
    fi

    # Validate required configuration
    if [[ -z "${SATELLITE_HOST}" ]]; then
        log_error "SATELLITE_HOST is required"
        exit 1
    fi

    log_info "Satellite Host: ${SATELLITE_HOST}"
    log_info "Organization: ${ORGANIZATION}"
    log_info "Lifecycle Environment: ${LIFECYCLE_ENV}"
    log_info "Base Content View: ${BASE_CONTENT_VIEW}"
    log_info "ELS Content View: ${ELS_CONTENT_VIEW}"
    log_info "Node3: ${NODE3_HOST}"
    log_info "Execution Mode: ${EXECUTION_MODE}"
    log_info "Dry Run: ${DRY_RUN}"
}

main() {
    parse_arguments "$@"

    log_info "=== RHEL 7 ELS Demo Environment Rebuild ==="
    log_info "Start time: $(date)"
    log_info "Log file: ${LOG_FILE}"

    load_or_prompt_config

    case "${EXECUTION_MODE}" in
        baseline)
            log_info "=== Running Baseline Setup ==="
            log_info "NOTE: This creates ELS Content View but does NOT assign node3 to it"
            log_info "      node3 will remain on base Content View for baseline measurement"

            # shellcheck source=scripts/els/satellite-iop.sh
            source "${SCRIPT_DIR}/scripts/els/satellite-iop.sh"
            # shellcheck source=scripts/els/insights-client.sh
            source "${SCRIPT_DIR}/scripts/els/insights-client.sh"
            # shellcheck source=scripts/els/els-repository.sh
            source "${SCRIPT_DIR}/scripts/els/els-repository.sh"
            # shellcheck source=scripts/els/content-view.sh
            source "${SCRIPT_DIR}/scripts/els/content-view.sh"
            # shellcheck source=scripts/els/grouper-optimization.sh
            source "${SCRIPT_DIR}/scripts/els/grouper-optimization.sh"
            # shellcheck source=scripts/els/vmaas-sync.sh
            source "${SCRIPT_DIR}/scripts/els/vmaas-sync.sh"
            # shellcheck source=scripts/els/baseline-capture.sh"
            source "${SCRIPT_DIR}/scripts/els/baseline-capture.sh"

            setup_satellite_iop
            setup_insights_client_all_nodes
            setup_els_repository
            create_els_content_view  # Creates CV but does NOT assign node3
            setup_grouper_optimization
            sync_vmaas_all
            trigger_insights_for_baseline  # Re-upload Insights data and wait for evaluation
            capture_baseline
            ;;

        enable-els)
            log_info "=== Enabling ELS on node3 ==="
            log_info "Processing order follows verified successful sequence:"
            log_info "  1. Assign node3 to ELS Content View"
            log_info "  2. Enable ELS repository on node3"
            log_info "  3. VMaaS synchronization"
            log_info "  4. Verify Grouper settings"
            log_info "  5. Send Insights data and wait for evaluation"
            log_info "  6. Verify ELS CVE detection"

            # shellcheck source=scripts/els/content-view.sh
            source "${SCRIPT_DIR}/scripts/els/content-view.sh"
            # shellcheck source=scripts/els/node-setup.sh
            source "${SCRIPT_DIR}/scripts/els/node-setup.sh"
            # shellcheck source=scripts/els/vmaas-sync.sh
            source "${SCRIPT_DIR}/scripts/els/vmaas-sync.sh"
            # shellcheck source=scripts/els/grouper-optimization.sh
            source "${SCRIPT_DIR}/scripts/els/grouper-optimization.sh"
            # shellcheck source=scripts/els/verify-results.sh
            source "${SCRIPT_DIR}/scripts/els/verify-results.sh"

            # Step 1: Assign node3 to ELS Content View
            assign_node3_to_els_content_view

            # Step 2-4: Enable ELS repository on node3
            enable_els_repository_on_node3

            # Step 5-8: VMaaS synchronization
            sync_vmaas_all

            # Step 9: Verify Grouper configuration
            verify_grouper_optimization

            # Step 10-11: Send Insights and wait for evaluation
            send_insights_and_wait_evaluation

            # Step 12: Verify ELS CVE detection
            verify_els_cves
            ;;

        rollback-grouper)
            log_info "=== Rolling back Grouper optimization settings ==="
            # shellcheck source=scripts/els/grouper-optimization.sh
            source "${SCRIPT_DIR}/scripts/els/grouper-optimization.sh"

            rollback_grouper_optimization
            ;;

        full)
            log_info "=== Running Full Setup ==="
            # Run baseline first
            export EXECUTION_MODE="baseline"
            main --baseline

            # Then enable ELS
            export EXECUTION_MODE="enable-els"
            main --enable-els
            ;;

        *)
            log_error "Unknown execution mode: ${EXECUTION_MODE}"
            exit 1
            ;;
    esac

    log_info "=== Completed ==="
    log_info "End time: $(date)"
    log_success "ELS demo environment rebuild completed successfully"
}

# Run main function
main "$@"
