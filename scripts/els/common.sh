#!/bin/bash
# Common functions for ELS demo rebuild scripts

# Colors for output
readonly COLOR_INFO='\033[0;36m'
readonly COLOR_SUCCESS='\033[0;32m'
readonly COLOR_WARNING='\033[1;33m'
readonly COLOR_ERROR='\033[0;31m'
readonly COLOR_RESET='\033[0m'

# Logging functions
log_info() {
    echo -e "${COLOR_INFO}[INFO]${COLOR_RESET} $*" | tee -a "${LOG_FILE:-/dev/null}"
}

log_success() {
    echo -e "${COLOR_SUCCESS}[SUCCESS]${COLOR_RESET} $*" | tee -a "${LOG_FILE:-/dev/null}"
}

log_warning() {
    echo -e "${COLOR_WARNING}[WARNING]${COLOR_RESET} $*" | tee -a "${LOG_FILE:-/dev/null}"
}

log_error() {
    echo -e "${COLOR_ERROR}[ERROR]${COLOR_RESET} $*" >&2 | tee -a "${LOG_FILE:-/dev/null}"
}

# Check if running in dry-run mode
is_dry_run() {
    [[ "${DRY_RUN:-false}" == "true" ]]
}

# Execute command on Satellite via SSH
satellite_exec() {
    local cmd="$1"
    local description="${2:-Execute command on Satellite}"

    if is_dry_run; then
        log_info "[DRY-RUN] Would execute on Satellite: ${description}"
        log_info "[DRY-RUN] Command: ${cmd}"
        return 0
    fi

    log_info "Executing on Satellite: ${description}"

    # Use existing SSH configuration
    ssh -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR \
        "${SATELLITE_USER}@${SATELLITE_HOST}" \
        "sudo bash -c '${cmd}'" 2>&1 | tee -a "${LOG_FILE:-/dev/null}"

    local exit_code=${PIPESTATUS[0]}
    if [[ ${exit_code} -ne 0 ]]; then
        log_error "Failed: ${description} (exit code: ${exit_code})"
        return ${exit_code}
    fi

    return 0
}

# Execute command on node via SSH
node_exec() {
    local node="$1"
    local cmd="$2"
    local description="${3:-Execute command on ${node}}"

    if is_dry_run; then
        log_info "[DRY-RUN] Would execute on ${node}: ${description}"
        log_info "[DRY-RUN] Command: ${cmd}"
        return 0
    fi

    log_info "Executing on ${node}: ${description}"

    local node_user="${NODE_USER:-ec2-user}"
    ssh -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR \
        "${node_user}@${node}" \
        "sudo bash -c '${cmd}'" 2>&1 | tee -a "${LOG_FILE:-/dev/null}"

    local exit_code=${PIPESTATUS[0]}
    if [[ ${exit_code} -ne 0 ]]; then
        log_error "Failed: ${description} (exit code: ${exit_code})"
        return ${exit_code}
    fi

    return 0
}

# Wait for process with timeout
wait_for_completion() {
    local check_cmd="$1"
    local description="$2"
    local timeout="${3:-300}"
    local interval="${4:-10}"

    log_info "Waiting for: ${description} (timeout: ${timeout}s)"

    local elapsed=0
    while [[ ${elapsed} -lt ${timeout} ]]; do
        if eval "${check_cmd}"; then
            log_success "Completed: ${description}"
            return 0
        fi

        sleep "${interval}"
        elapsed=$((elapsed + interval))
        log_info "Still waiting... (${elapsed}/${timeout}s)"
    done

    log_error "Timeout waiting for: ${description}"
    return 1
}

# Get Hammer value
hammer_get() {
    local resource="$1"
    local filters="$2"
    local field="$3"

    satellite_exec "hammer --output json ${resource} list ${filters} 2>/dev/null | jq -r '.[0].${field} // empty'" \
        "Get ${field} from ${resource}"
}

# Check if service is active
check_service_active() {
    local service="$1"
    satellite_exec "systemctl is-active ${service}" "Check ${service} status" >/dev/null 2>&1
}

# Prompt for input
prompt_input() {
    local prompt="$1"
    local default="$2"
    local secret="${3:-false}"
    local value

    if [[ -n "${default}" ]]; then
        prompt="${prompt} [${default}]"
    fi

    if [[ "${secret}" == "true" ]]; then
        read -r -s -p "${prompt}: " value
        echo
    else
        read -r -p "${prompt}: " value
    fi

    if [[ -z "${value}" && -n "${default}" ]]; then
        value="${default}"
    fi

    echo "${value}"
}

# Load configuration from file
load_config() {
    local config_file="$1"

    if [[ ! -f "${config_file}" ]]; then
        log_error "Configuration file not found: ${config_file}"
        return 1
    fi

    # shellcheck source=/dev/null
    source "${config_file}"
    return 0
}
