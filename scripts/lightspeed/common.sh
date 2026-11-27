#!/bin/bash
# Common functions for Lightspeed on-premises setup automation

set -euo pipefail

# Color codes for output
readonly COLOR_RESET='\033[0m'
readonly COLOR_RED='\033[0;31m'
readonly COLOR_GREEN='\033[0;32m'
readonly COLOR_YELLOW='\033[1;33m'
readonly COLOR_BLUE='\033[0;34m'
readonly COLOR_CYAN='\033[0;36m'

# Log file
LOG_FILE="${LOG_FILE:-}"

# Dry-run mode flag
DRY_RUN="${DRY_RUN:-false}"

# Timestamp for this execution
EXECUTION_START_TIME=""

# Function to initialize execution timestamp
init_execution_timestamp() {
    EXECUTION_START_TIME=$(date '+%Y-%m-%d %H:%M:%S')
    export EXECUTION_START_TIME
}

# Logging functions
log_message() {
    local level="$1"
    shift
    local message="$*"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')

    if [[ -n "${LOG_FILE}" ]]; then
        echo "[${timestamp}] [${level}] ${message}" >> "${LOG_FILE}"
    fi

    echo -e "${message}"
}

log_info() {
    log_message "INFO" "${COLOR_BLUE}[INFO]${COLOR_RESET} $*"
}

log_success() {
    log_message "SUCCESS" "${COLOR_GREEN}[✓]${COLOR_RESET} $*"
}

log_warn() {
    log_message "WARN" "${COLOR_YELLOW}[!]${COLOR_RESET} $*"
}

log_error() {
    log_message "ERROR" "${COLOR_RED}[✗]${COLOR_RESET} $*" >&2
}

log_step() {
    log_message "STEP" "${COLOR_CYAN}[→]${COLOR_RESET} $*"
}

log_skip() {
    log_message "SKIP" "${COLOR_YELLOW}[SKIP]${COLOR_RESET} $*"
}

# Error exit function
error_exit() {
    log_error "$@"
    exit 1
}

# Check if running in dry-run mode
is_dry_run() {
    [[ "${DRY_RUN}" == "true" ]]
}

# Load configuration from file
load_config() {
    local config_file="$1"

    if [[ ! -f "${config_file}" ]]; then
        log_warn "Configuration file not found: ${config_file}"
        return 1
    fi

    log_info "Loading configuration from ${config_file}"

    # Source the config file
    # shellcheck disable=SC1090
    source "${config_file}"

    return 0
}

# Validate required environment variables
validate_required_vars() {
    local missing_vars=()

    for var in "$@"; do
        if [[ -z "${!var:-}" ]]; then
            missing_vars+=("${var}")
        fi
    done

    if [[ ${#missing_vars[@]} -gt 0 ]]; then
        error_exit "Missing required variables: ${missing_vars[*]}"
    fi
}

# Test SSH connection to bastion
test_bastion_connection() {
    local bastion_user="${1:-}"
    local bastion_host="${2:-}"
    local bastion_port="${3:-22}"

    log_step "Testing SSH connection to bastion: ${bastion_user}@${bastion_host}:${bastion_port}"

    if ! ssh -p "${bastion_port}" \
            -o BatchMode=yes \
            -o ConnectTimeout=10 \
            -o StrictHostKeyChecking=accept-new \
            "${bastion_user}@${bastion_host}" \
            "echo 'Connection successful'" > /dev/null 2>&1; then
        log_error "Failed to connect to bastion host"
        return 1
    fi

    log_success "Bastion connection test passed"
    return 0
}

# Test SSH connection from bastion to target host
test_target_connection() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="${3:-22}"
    local target_user="$4"
    local target_host="$5"

    log_step "Testing SSH connection from bastion to ${target_user}@${target_host}"

    local test_cmd="ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new ${target_user}@${target_host} 'echo Connection successful'"

    if ! ssh -p "${bastion_port}" \
            -o BatchMode=yes \
            -o ConnectTimeout=10 \
            -o StrictHostKeyChecking=accept-new \
            "${bastion_user}@${bastion_host}" \
            "${test_cmd}" > /dev/null 2>&1; then
        log_error "Failed to connect from bastion to ${target_host}"
        return 1
    fi

    log_success "Target connection test passed for ${target_host}"
    return 0
}

# Test passwordless sudo on target host
test_passwordless_sudo() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="${3:-22}"
    local target_user="$4"
    local target_host="$5"

    log_step "Testing passwordless sudo on ${target_host}"

    local sudo_cmd="ssh -o BatchMode=yes -o ConnectTimeout=10 ${target_user}@${target_host} 'sudo -n true'"

    if ! ssh -p "${bastion_port}" \
            -o BatchMode=yes \
            -o ConnectTimeout=10 \
            "${bastion_user}@${bastion_host}" \
            "${sudo_cmd}" > /dev/null 2>&1; then
        log_error "Passwordless sudo not available on ${target_host}"
        return 1
    fi

    log_success "Passwordless sudo test passed on ${target_host}"
    return 0
}

# Execute command on target host via bastion
# Returns: stdout of the command
execute_remote_command() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local target_user="$4"
    local target_host="$5"
    local command="$6"
    local use_sudo="${7:-false}"

    local full_command="${command}"
    if [[ "${use_sudo}" == "true" ]]; then
        full_command="sudo -n bash -c $(printf '%q' "${command}")"
    fi

    local ssh_cmd="ssh -o BatchMode=yes -o ConnectTimeout=30 ${target_user}@${target_host} $(printf '%q' "${full_command}")"

    if is_dry_run; then
        log_info "[DRY-RUN] Would execute on ${target_host}: ${command}"
        return 0
    fi

    local output
    if ! output=$(ssh -p "${bastion_port}" \
                      -o BatchMode=yes \
                      -o ConnectTimeout=30 \
                      "${bastion_user}@${bastion_host}" \
                      "${ssh_cmd}" 2>&1); then
        log_error "Command failed on ${target_host}: ${command}"
        log_error "Output: ${output}"
        return 1
    fi

    echo "${output}"
    return 0
}

# Execute command with output capture and logging
execute_and_log() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local target_user="$4"
    local target_host="$5"
    local command="$6"
    local use_sudo="${7:-false}"
    local description="${8:-Executing command}"

    log_step "${description}"

    local output
    if output=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                       "${target_user}" "${target_host}" \
                                       "${command}" "${use_sudo}"); then
        if [[ -n "${output}" ]]; then
            echo "${output}"
        fi
        return 0
    else
        return 1
    fi
}

# Prompt for user input
prompt_input() {
    local prompt_message="$1"
    local default_value="${2:-}"
    local is_secret="${3:-false}"
    local result

    if [[ -n "${default_value}" ]]; then
        prompt_message="${prompt_message} [${default_value}]"
    fi

    if [[ "${is_secret}" == "true" ]]; then
        read -r -s -p "${prompt_message}: " result
        echo >&2
    else
        read -r -p "${prompt_message}: " result
    fi

    if [[ -z "${result}" && -n "${default_value}" ]]; then
        result="${default_value}"
    fi

    echo "${result}"
}

# Confirm action
confirm_action() {
    local message="$1"
    local response

    read -r -p "${message} [y/N]: " response
    case "${response}" in
        [yY]|[yY][eE][sS])
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

# Check if command exists on remote host
check_remote_command() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local target_user="$4"
    local target_host="$5"
    local command_name="$6"

    if execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${target_user}" "${target_host}" \
                               "command -v ${command_name}" "false" > /dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# Sanitize output for logging (remove potential secrets)
sanitize_output() {
    local text="$1"

    # Mask potential passwords, tokens, and keys
    text=$(echo "${text}" | sed -E 's/(password|token|key|secret)[[:space:]]*[:=][[:space:]]*[^[:space:]]+/\1=***REDACTED***/gi')

    echo "${text}"
}

# Create timestamped backup of a file on remote host
backup_remote_file() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local target_user="$4"
    local target_host="$5"
    local file_path="$6"

    local timestamp
    timestamp=$(date '+%Y%m%d_%H%M%S')
    local backup_path="${file_path}.backup.${timestamp}"

    if is_dry_run; then
        log_info "[DRY-RUN] Would backup ${file_path} to ${backup_path}"
        return 0
    fi

    log_step "Backing up ${file_path} to ${backup_path}"

    if execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${target_user}" "${target_host}" \
                               "cp -p ${file_path} ${backup_path}" "true" > /dev/null 2>&1; then
        log_success "Backup created: ${backup_path}"
        echo "${backup_path}"
        return 0
    else
        log_error "Failed to backup ${file_path}"
        return 1
    fi
}

# Wait for a condition with timeout
wait_for_condition() {
    local condition_function="$1"
    local timeout_seconds="${2:-300}"
    local check_interval="${3:-5}"
    local description="${4:-Waiting for condition}"

    log_step "${description} (timeout: ${timeout_seconds}s)"

    local elapsed=0
    while (( elapsed < timeout_seconds )); do
        if ${condition_function}; then
            log_success "Condition met after ${elapsed}s"
            return 0
        fi

        sleep "${check_interval}"
        elapsed=$((elapsed + check_interval))
    done

    log_error "Timeout waiting for condition after ${timeout_seconds}s"
    return 1
}

# Export functions for use in subshells
export -f log_message log_info log_success log_warn log_error log_step log_skip
export -f error_exit is_dry_run validate_required_vars
export -f test_bastion_connection test_target_connection test_passwordless_sudo
export -f execute_remote_command execute_and_log
export -f prompt_input confirm_action
export -f check_remote_command sanitize_output backup_remote_file wait_for_condition
