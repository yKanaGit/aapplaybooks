#!/bin/bash
# Main script for Lightspeed on-premises setup automation
# Automates the re-deployment of Lightspeed on-premises after RHDP environment re-provisioning

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/lightspeed/common.sh
source "${SCRIPT_DIR}/scripts/lightspeed/common.sh"

# Default configuration
CONFIG_FILE="${SCRIPT_DIR}/lightspeed.env"
CONFIG_EXAMPLE="${SCRIPT_DIR}/config/lightspeed.env.example"

# Default values
BASTION_USER="${BASTION_USER:-}"
BASTION_HOST="${BASTION_HOST:-}"
BASTION_PORT="${BASTION_PORT:-22}"
SATELLITE_USER="${SATELLITE_USER:-ec2-user}"
SATELLITE_HOST="${SATELLITE_HOST:-satellite}"
NODE1_USER="${NODE1_USER:-ec2-user}"
NODE1_HOST="${NODE1_HOST:-node1}"
NODE1_HOSTNAME="${NODE1_HOSTNAME:-node1.example.com}"

# Execution mode
EXECUTION_MODE="full"  # full, satellite-only, client-only, verify
DRY_RUN=false

# Log file
LOG_FILE="${SCRIPT_DIR}/lightspeed-$(date +%Y%m%d_%H%M%S).log"

# Show usage
show_usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Automate Lightspeed on-premises setup for RHDP environments.

OPTIONS:
    --dry-run              Perform checks without making changes
    --satellite-only       Only setup Satellite (skip node1 Insights Client setup)
    --client-only          Only setup node1 Insights Client (skip Satellite setup)
    --verify               Only verify current setup status
    --config FILE          Use specified configuration file (default: lightspeed.env)
    --help                 Show this help message

EXAMPLES:
    # Dry run to check what would be done
    ./setup-lightspeed.sh --dry-run

    # Full setup
    ./setup-lightspeed.sh

    # Setup Satellite only
    ./setup-lightspeed.sh --satellite-only

    # Setup Insights Client only
    ./setup-lightspeed.sh --client-only

    # Verify current status
    ./setup-lightspeed.sh --verify

CONFIGURATION:
    Configuration can be provided via:
    1. Environment variables
    2. Configuration file (lightspeed.env)
    3. Interactive prompts during execution

    Required configuration:
    - BASTION_USER: SSH user for bastion host
    - BASTION_HOST: Bastion hostname or IP
    - BASTION_PORT: Bastion SSH port (default: 22)

    Optional configuration (with defaults):
    - SATELLITE_USER: SSH user for Satellite (default: ec2-user)
    - SATELLITE_HOST: Satellite internal hostname (default: satellite)
    - NODE1_USER: SSH user for node1 (default: ec2-user)
    - NODE1_HOST: node1 internal hostname (default: node1)
    - NODE1_HOSTNAME: node1 FQDN (default: node1.example.com)

For detailed instructions, see: docs/lightspeed-setup.md
EOF
}

# Parse command line arguments
parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dry-run)
                DRY_RUN=true
                export DRY_RUN
                shift
                ;;
            --satellite-only)
                EXECUTION_MODE="satellite-only"
                shift
                ;;
            --client-only)
                EXECUTION_MODE="client-only"
                shift
                ;;
            --verify)
                EXECUTION_MODE="verify"
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

# Load configuration from file or prompt
load_or_prompt_config() {
    log_info "=== Configuration ==="

    # Try to load configuration file
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

    # Prompt for missing required configuration
    if [[ -z "${BASTION_USER}" ]]; then
        BASTION_USER=$(prompt_input "Enter bastion SSH username" "" "false")
    fi

    if [[ -z "${BASTION_HOST}" ]]; then
        BASTION_HOST=$(prompt_input "Enter bastion hostname or IP" "" "false")
    fi

    if [[ -z "${BASTION_PORT}" ]]; then
        BASTION_PORT=$(prompt_input "Enter bastion SSH port" "22" "false")
    fi

    # Prompt for optional configuration with defaults
    SATELLITE_USER=$(prompt_input "Enter Satellite SSH username" "${SATELLITE_USER}" "false")
    SATELLITE_HOST=$(prompt_input "Enter Satellite internal hostname" "${SATELLITE_HOST}" "false")

    if [[ "${EXECUTION_MODE}" != "satellite-only" && "${EXECUTION_MODE}" != "verify" ]]; then
        NODE1_USER=$(prompt_input "Enter node1 SSH username" "${NODE1_USER}" "false")
        NODE1_HOST=$(prompt_input "Enter node1 internal hostname" "${NODE1_HOST}" "false")
        NODE1_HOSTNAME=$(prompt_input "Enter node1 FQDN" "${NODE1_HOSTNAME}" "false")
    fi

    # Validate required configuration
    validate_required_vars BASTION_USER BASTION_HOST BASTION_PORT SATELLITE_USER SATELLITE_HOST

    if [[ "${EXECUTION_MODE}" != "satellite-only" && "${EXECUTION_MODE}" != "verify" ]]; then
        validate_required_vars NODE1_USER NODE1_HOST NODE1_HOSTNAME
    fi

    # Display configuration
    log_info "Configuration summary:"
    log_info "  Bastion: ${BASTION_USER}@${BASTION_HOST}:${BASTION_PORT}"
    log_info "  Satellite: ${SATELLITE_USER}@${SATELLITE_HOST}"
    if [[ "${EXECUTION_MODE}" != "satellite-only" ]]; then
        log_info "  node1: ${NODE1_USER}@${NODE1_HOST} (${NODE1_HOSTNAME})"
    fi
    log_info "  Mode: ${EXECUTION_MODE}"
    log_info "  Dry run: ${DRY_RUN}"
    log_info "  Log file: ${LOG_FILE}"

    # Confirm before proceeding (unless in dry-run or verify mode)
    if [[ "${DRY_RUN}" == "false" && "${EXECUTION_MODE}" != "verify" ]]; then
        echo ""
        if ! confirm_action "Do you want to proceed with this configuration?"; then
            log_info "Setup cancelled by user"
            exit 0
        fi
    fi
}

# Test SSH connections
test_ssh_connections() {
    log_info "=== Testing SSH Connections ==="

    # Test bastion connection
    if ! test_bastion_connection "${BASTION_USER}" "${BASTION_HOST}" "${BASTION_PORT}"; then
        error_exit "Failed to connect to bastion host. Please check your SSH configuration."
    fi

    # Test Satellite connection
    if ! test_target_connection "${BASTION_USER}" "${BASTION_HOST}" "${BASTION_PORT}" \
                                 "${SATELLITE_USER}" "${SATELLITE_HOST}"; then
        error_exit "Failed to connect to Satellite. Please check your SSH configuration."
    fi

    # Test passwordless sudo on Satellite
    if ! test_passwordless_sudo "${BASTION_USER}" "${BASTION_HOST}" "${BASTION_PORT}" \
                                 "${SATELLITE_USER}" "${SATELLITE_HOST}"; then
        error_exit "Passwordless sudo is not available on Satellite. Please configure sudo access."
    fi

    # Test node1 connection (if needed)
    if [[ "${EXECUTION_MODE}" != "satellite-only" && "${EXECUTION_MODE}" != "verify" ]]; then
        if ! test_target_connection "${BASTION_USER}" "${BASTION_HOST}" "${BASTION_PORT}" \
                                     "${NODE1_USER}" "${NODE1_HOST}"; then
            error_exit "Failed to connect to node1. Please check your SSH configuration."
        fi

        if ! test_passwordless_sudo "${BASTION_USER}" "${BASTION_HOST}" "${BASTION_PORT}" \
                                     "${NODE1_USER}" "${NODE1_HOST}"; then
            error_exit "Passwordless sudo is not available on node1. Please configure sudo access."
        fi
    fi

    log_success "All SSH connection tests passed"
}

# Run Satellite setup
run_satellite_setup_phase() {
    log_info ""
    log_info "========================================"
    log_info "Phase 1: Satellite Pre-checks"
    log_info "========================================"

    # shellcheck source=scripts/lightspeed/precheck.sh
    source "${SCRIPT_DIR}/scripts/lightspeed/precheck.sh"

    if ! run_precheck "${BASTION_USER}" "${BASTION_HOST}" "${BASTION_PORT}" \
                      "${SATELLITE_USER}" "${SATELLITE_HOST}"; then
        error_exit "Pre-checks failed. Please resolve the issues before proceeding."
    fi

    log_info ""
    log_info "========================================"
    log_info "Phase 2: Satellite Setup"
    log_info "========================================"

    # shellcheck source=scripts/lightspeed/setup-satellite.sh
    source "${SCRIPT_DIR}/scripts/lightspeed/setup-satellite.sh"

    if ! run_satellite_setup "${BASTION_USER}" "${BASTION_HOST}" "${BASTION_PORT}" \
                             "${SATELLITE_USER}" "${SATELLITE_HOST}"; then
        error_exit "Satellite setup failed"
    fi

    log_info ""
    log_info "========================================"
    log_info "Phase 3: Vulnerability Sync"
    log_info "========================================"

    # shellcheck source=scripts/lightspeed/sync-vulnerability.sh
    source "${SCRIPT_DIR}/scripts/lightspeed/sync-vulnerability.sh"

    if ! run_vulnerability_sync "${BASTION_USER}" "${BASTION_HOST}" "${BASTION_PORT}" \
                                "${SATELLITE_USER}" "${SATELLITE_HOST}"; then
        log_warn "Vulnerability sync completed with warnings"
    fi
}

# Run node1 Insights Client setup
run_client_setup_phase() {
    log_info ""
    log_info "========================================"
    log_info "Phase 4: Insights Client Setup"
    log_info "========================================"

    # shellcheck source=scripts/lightspeed/setup-insights-client.sh
    source "${SCRIPT_DIR}/scripts/lightspeed/setup-insights-client.sh"

    if ! run_insights_client_setup "${BASTION_USER}" "${BASTION_HOST}" "${BASTION_PORT}" \
                                   "${SATELLITE_USER}" "${SATELLITE_HOST}" \
                                   "${NODE1_USER}" "${NODE1_HOST}" \
                                   "${NODE1_HOSTNAME}"; then
        error_exit "Insights Client setup failed"
    fi
}

# Run verification
run_verification_phase() {
    log_info ""
    log_info "========================================"
    log_info "Verification"
    log_info "========================================"

    # shellcheck source=scripts/lightspeed/verify.sh
    source "${SCRIPT_DIR}/scripts/lightspeed/verify.sh"

    local report_file="${SCRIPT_DIR}/lightspeed-report.json"

    if [[ "${EXECUTION_MODE}" == "satellite-only" ]]; then
        generate_verification_report "${BASTION_USER}" "${BASTION_HOST}" "${BASTION_PORT}" \
                                     "${SATELLITE_USER}" "${SATELLITE_HOST}" \
                                     "" "" "${report_file}"
    else
        generate_verification_report "${BASTION_USER}" "${BASTION_HOST}" "${BASTION_PORT}" \
                                     "${SATELLITE_USER}" "${SATELLITE_HOST}" \
                                     "${NODE1_USER}" "${NODE1_HOST}" \
                                     "${report_file}"
    fi
}

# Main execution
main() {
    echo ""
    log_info "========================================"
    log_info "Lightspeed on-premises Setup Automation"
    log_info "========================================"
    echo ""

    # Initialize execution timestamp
    init_execution_timestamp

    # Parse command line arguments
    parse_arguments "$@"

    # Load or prompt for configuration
    load_or_prompt_config

    # Test SSH connections
    test_ssh_connections

    # Execute based on mode
    case "${EXECUTION_MODE}" in
        full)
            run_satellite_setup_phase
            run_client_setup_phase
            run_verification_phase
            ;;
        satellite-only)
            run_satellite_setup_phase
            run_verification_phase
            ;;
        client-only)
            run_client_setup_phase
            run_verification_phase
            ;;
        verify)
            run_verification_phase
            ;;
    esac

    log_info ""
    log_info "========================================"
    log_info "Setup Complete"
    log_info "========================================"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "This was a dry run. No changes were made to the remote systems."
        log_info "To perform the actual setup, run without --dry-run flag."
    else
        log_success "Lightspeed on-premises setup completed successfully"
        log_info "Log file: ${LOG_FILE}"
        if [[ -f "${SCRIPT_DIR}/lightspeed-report.json" ]]; then
            log_info "Report file: ${SCRIPT_DIR}/lightspeed-report.json"
        fi
    fi

    echo ""
}

# Execute main function
main "$@"
