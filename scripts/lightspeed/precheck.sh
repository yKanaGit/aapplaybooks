#!/bin/bash
# Satellite pre-installation checks for Lightspeed on-premises

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lightspeed/common.sh
source "${SCRIPT_DIR}/common.sh"

# Check Satellite version
check_satellite_version() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Checking Satellite version"

    local version
    version=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                     "${satellite_user}" "${satellite_host}" \
                                     "rpm -q satellite --qf '%{VERSION}' 2>/dev/null || echo 'unknown'" "false")

    if [[ "${version}" == "unknown" ]]; then
        log_warn "Satellite package not found, checking katello-common"
        version=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                         "${satellite_user}" "${satellite_host}" \
                                         "rpm -q katello-common --qf '%{VERSION}' 2>/dev/null || echo 'unknown'" "false")
    fi

    if [[ "${version}" == "unknown" ]]; then
        log_error "Could not determine Satellite version"
        return 1
    fi

    log_success "Satellite version: ${version}"
    echo "${version}"
    return 0
}

# Check OS version
check_os_version() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Checking OS version"

    local os_version
    os_version=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                        "${satellite_user}" "${satellite_host}" \
                                        "cat /etc/redhat-release" "false")

    log_success "OS: ${os_version}"
    echo "${os_version}"
    return 0
}

# Check system resources
check_system_resources() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Checking system resources"

    local cpu_count
    cpu_count=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                       "${satellite_user}" "${satellite_host}" \
                                       "nproc" "false")
    log_info "CPU count: ${cpu_count}"

    local mem_total
    mem_total=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                       "${satellite_user}" "${satellite_host}" \
                                       "free -h | grep Mem: | awk '{print \$2}'" "false")
    log_info "Total memory: ${mem_total}"

    local disk_free
    disk_free=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                       "${satellite_user}" "${satellite_host}" \
                                       "df -h / | tail -1 | awk '{print \$4}'" "false")
    log_info "Disk free (root): ${disk_free}"

    local var_free
    var_free=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                      "${satellite_user}" "${satellite_host}" \
                                      "df -h /var | tail -1 | awk '{print \$4}'" "false")
    log_info "Disk free (/var): ${var_free}"

    echo "CPU:${cpu_count} MEM:${mem_total} DISK_ROOT:${disk_free} DISK_VAR:${var_free}"
    return 0
}

# Get FQDN
get_satellite_fqdn() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Getting Satellite FQDN"

    local fqdn
    fqdn=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                  "${satellite_user}" "${satellite_host}" \
                                  "hostname -f" "false")

    log_success "FQDN: ${fqdn}"
    echo "${fqdn}"
    return 0
}

# Get primary IPv4 address
get_primary_ipv4() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Getting primary IPv4 address"

    local ipv4
    ipv4=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                  "${satellite_user}" "${satellite_host}" \
                                  "hostname -I | awk '{print \$1}'" "false")

    if [[ -z "${ipv4}" ]]; then
        log_error "Could not determine primary IPv4 address"
        return 1
    fi

    log_success "Primary IPv4: ${ipv4}"
    echo "${ipv4}"
    return 0
}

# Check Podman network backend
check_podman_network_backend() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Checking Podman network backend"

    local backend
    backend=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                     "${satellite_user}" "${satellite_host}" \
                                     "podman info --format '{{.Host.NetworkBackend}}' 2>/dev/null || echo 'unknown'" "false")

    if [[ "${backend}" == "unknown" ]]; then
        log_warn "Could not determine Podman network backend"
        return 1
    fi

    log_success "Podman network backend: ${backend}"

    if [[ "${backend}" != "netavark" ]]; then
        log_error "Podman network backend is ${backend}, but netavark is required for IOP"
        return 1
    fi

    echo "${backend}"
    return 0
}

# Check IOP installation status
check_iop_installation() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Checking IOP installation status"

    local iop_packages
    iop_packages=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                          "${satellite_user}" "${satellite_host}" \
                                          "rpm -qa | grep -E 'foreman-iop|satellite-iop' | sort" "false" || echo "")

    if [[ -z "${iop_packages}" ]]; then
        log_info "IOP packages not installed"
        echo "not_installed"
        return 0
    fi

    log_success "IOP packages found:"
    echo "${iop_packages}" | while IFS= read -r pkg; do
        log_info "  - ${pkg}"
    done

    echo "installed"
    return 0
}

# Check IOP container status
check_iop_containers() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Checking IOP container status"

    local container_count
    container_count=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                             "${satellite_user}" "${satellite_host}" \
                                             "sudo -n podman ps --filter 'label=com.redhat.component' --format '{{.Names}}' | grep -c iop || echo 0" "false")

    if [[ "${container_count}" -eq 0 ]]; then
        log_info "No IOP containers running"
        echo "0"
        return 0
    fi

    log_success "IOP containers running: ${container_count}"

    local container_list
    container_list=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                            "${satellite_user}" "${satellite_host}" \
                                            "sudo -n podman ps --filter 'label=com.redhat.component' --format '{{.Names}}' | grep iop | sort" "false" || echo "")

    if [[ -n "${container_list}" ]]; then
        echo "${container_list}" | while IFS= read -r container; do
            log_info "  - ${container}"
        done
    fi

    echo "${container_count}"
    return 0
}

# Check network connectivity
check_network_connectivity() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"
    local target_host="$6"
    local target_port="${7:-443}"

    log_step "Checking connectivity to ${target_host}:${target_port}"

    if execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${satellite_user}" "${satellite_host}" \
                               "timeout 10 bash -c '</dev/tcp/${target_host}/${target_port}' 2>/dev/null" "false" > /dev/null 2>&1; then
        log_success "Connectivity to ${target_host}:${target_port} OK"
        return 0
    else
        log_error "Cannot connect to ${target_host}:${target_port}"
        return 1
    fi
}

# Check satellite-installer options
check_satellite_installer_options() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Checking satellite-installer for --enable-iop option"

    # Check if --enable-iop option exists (without running --full-help to avoid password exposure)
    if execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${satellite_user}" "${satellite_host}" \
                               "sudo -n satellite-installer --help 2>&1 | grep -q -- '--enable-iop'" "false" > /dev/null 2>&1; then
        log_success "satellite-installer supports --enable-iop option"
        return 0
    else
        log_error "satellite-installer does not support --enable-iop option"
        return 1
    fi
}

# Check Hammer CLI availability
check_hammer_cli() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Checking Hammer CLI availability"

    if ! check_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${satellite_user}" "${satellite_host}" \
                               "hammer"; then
        log_error "Hammer CLI not found"
        return 1
    fi

    local hammer_version
    hammer_version=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                            "${satellite_user}" "${satellite_host}" \
                                            "hammer --version 2>&1 | head -1" "false")

    log_success "Hammer CLI available: ${hammer_version}"
    return 0
}

# Main precheck function
run_precheck() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_info "=== Starting Satellite Pre-Installation Checks ==="

    local all_checks_passed=true

    # Basic system information
    if ! check_satellite_version "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                 "${satellite_user}" "${satellite_host}"; then
        all_checks_passed=false
    fi

    if ! check_os_version "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                          "${satellite_user}" "${satellite_host}"; then
        all_checks_passed=false
    fi

    if ! check_system_resources "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                 "${satellite_user}" "${satellite_host}"; then
        all_checks_passed=false
    fi

    if ! get_satellite_fqdn "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                            "${satellite_user}" "${satellite_host}"; then
        all_checks_passed=false
    fi

    if ! get_primary_ipv4 "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                          "${satellite_user}" "${satellite_host}"; then
        all_checks_passed=false
    fi

    # Podman and IOP checks
    if ! check_podman_network_backend "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                      "${satellite_user}" "${satellite_host}"; then
        all_checks_passed=false
    fi

    check_iop_installation "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                           "${satellite_user}" "${satellite_host}"

    check_iop_containers "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                         "${satellite_user}" "${satellite_host}"

    # Network connectivity
    if ! check_network_connectivity "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                    "${satellite_user}" "${satellite_host}" \
                                    "registry.redhat.io" "443"; then
        all_checks_passed=false
    fi

    if ! check_network_connectivity "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                    "${satellite_user}" "${satellite_host}" \
                                    "security.access.redhat.com" "443"; then
        all_checks_passed=false
    fi

    # Tool availability
    if ! check_satellite_installer_options "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                           "${satellite_user}" "${satellite_host}"; then
        all_checks_passed=false
    fi

    if ! check_hammer_cli "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                          "${satellite_user}" "${satellite_host}"; then
        all_checks_passed=false
    fi

    log_info "=== Pre-Installation Checks Complete ==="

    if [[ "${all_checks_passed}" == "true" ]]; then
        log_success "All pre-installation checks passed"
        return 0
    else
        log_error "Some pre-installation checks failed"
        return 1
    fi
}

# If script is executed directly (not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [[ $# -lt 5 ]]; then
        echo "Usage: $0 <bastion_user> <bastion_host> <bastion_port> <satellite_user> <satellite_host>" >&2
        exit 1
    fi

    run_precheck "$@"
fi
