#!/bin/bash
# Common functions for ELS demo rebuild scripts

# Colors for output
readonly COLOR_INFO='\033[0;36m'
readonly COLOR_SUCCESS='\033[0;32m'
readonly COLOR_WARNING='\033[1;33m'
readonly COLOR_ERROR='\033[0;31m'
readonly COLOR_RESET='\033[0m'

# Logging functions (all logs go to stderr to avoid mixing with command output)
log_info() {
    echo -e "${COLOR_INFO}[INFO]${COLOR_RESET} $*" | tee -a "${LOG_FILE:-/dev/null}" >&2
}

log_success() {
    echo -e "${COLOR_SUCCESS}[SUCCESS]${COLOR_RESET} $*" | tee -a "${LOG_FILE:-/dev/null}" >&2
}

log_warning() {
    echo -e "${COLOR_WARNING}[WARNING]${COLOR_RESET} $*" | tee -a "${LOG_FILE:-/dev/null}" >&2
}

log_error() {
    echo -e "${COLOR_ERROR}[ERROR]${COLOR_RESET} $*" | tee -a "${LOG_FILE:-/dev/null}" >&2
}

# Check if running in dry-run mode
is_dry_run() {
    [[ "${DRY_RUN:-false}" == "true" ]]
}

# Execute command on Satellite via SSH
# Output: Command stdout only (for use in command substitution)
# Logs: Sent to stderr
# Return: Command exit code
satellite_exec() {
    local cmd="$1"
    local description="${2:-Execute command on Satellite}"

    if is_dry_run; then
        log_info "[DRY-RUN] Would execute on Satellite: ${description}"
        log_info "[DRY-RUN] Command: ${cmd}"
        return 0
    fi

    log_info "Executing on Satellite: ${description}"

    # Pass command via stdin to avoid quoting issues
    # Redirect stderr to fd 3, then back to stderr after logging
    local output
    local exit_code

    output=$(ssh "${SATELLITE_USER}@${SATELLITE_HOST}" 'sudo bash' <<EOF 2>&3
${cmd}
EOF
)
    exit_code=$?

    # Log output to file only (not to stdout to avoid mixing with command output)
    if [[ -n "${output}" ]] && [[ -n "${LOG_FILE}" ]]; then
        echo "${output}" >> "${LOG_FILE}"
    fi

    # Print output to stdout (for command substitution)
    echo "${output}"

    if [[ ${exit_code} -ne 0 ]]; then
        log_error "Failed: ${description} (exit code: ${exit_code})"
        return ${exit_code}
    fi

    return 0
} 3>&2

# Execute command on node via SSH
# Output: Command stdout only (for use in command substitution)
# Logs: Sent to stderr
# Return: Command exit code
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

    # Pass command via stdin to avoid quoting issues
    local node_user="${NODE_USER:-ec2-user}"
    local output
    local exit_code

    output=$(ssh "${node_user}@${node}" 'sudo bash' <<EOF 2>&3
${cmd}
EOF
)
    exit_code=$?

    # Log output to file only
    if [[ -n "${output}" ]] && [[ -n "${LOG_FILE}" ]]; then
        echo "${output}" >> "${LOG_FILE}"
    fi

    # Print output to stdout
    echo "${output}"

    if [[ ${exit_code} -ne 0 ]]; then
        log_error "Failed: ${description} (exit code: ${exit_code})"
        return ${exit_code}
    fi

    return 0
} 3>&2

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
