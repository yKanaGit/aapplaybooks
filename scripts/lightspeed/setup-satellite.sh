#!/bin/bash
# Satellite setup for Lightspeed on-premises

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lightspeed/common.sh
source "${SCRIPT_DIR}/common.sh"

# Check DNS resolution mismatch
check_dns_mismatch() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Checking DNS resolution"

    local fqdn
    fqdn=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                  "${satellite_user}" "${satellite_host}" \
                                  "hostname -f" "false")

    local primary_ip
    primary_ip=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                        "${satellite_user}" "${satellite_host}" \
                                        "hostname -I | awk '{print \$1}'" "false")

    local resolved_ip
    resolved_ip=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                         "${satellite_user}" "${satellite_host}" \
                                         "getent hosts ${fqdn} | awk '{print \$1}' | head -1" "false" || echo "")

    log_info "FQDN: ${fqdn}"
    log_info "Primary IP (NIC): ${primary_ip}"
    log_info "Resolved IP (DNS): ${resolved_ip}"

    if [[ "${resolved_ip}" != "${primary_ip}" ]]; then
        log_warn "DNS mismatch detected: ${fqdn} resolves to ${resolved_ip} but NIC has ${primary_ip}"
        echo "mismatch:${fqdn}:${primary_ip}:${resolved_ip}"
        return 1
    else
        log_success "DNS resolution is correct"
        echo "match:${fqdn}:${primary_ip}"
        return 0
    fi
}

# Fix DNS configuration in /etc/hosts
fix_dns_configuration() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"
    local fqdn="$6"
    local ip_address="$7"

    log_step "Fixing DNS configuration in /etc/hosts"

    if is_dry_run; then
        log_info "[DRY-RUN] Would add '${ip_address} ${fqdn}' to /etc/hosts"
        return 0
    fi

    # Backup /etc/hosts
    if ! backup_remote_file "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                            "${satellite_user}" "${satellite_host}" \
                            "/etc/hosts"; then
        log_error "Failed to backup /etc/hosts"
        return 1
    fi

    # Check if entry already exists
    local existing_entry
    existing_entry=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                            "${satellite_user}" "${satellite_host}" \
                                            "grep -E '^${ip_address}[[:space:]]+${fqdn}([[:space:]]|$)' /etc/hosts || echo ''" "true" || echo "")

    if [[ -n "${existing_entry}" ]]; then
        log_success "Entry already exists in /etc/hosts: ${existing_entry}"
        return 0
    fi

    # Add entry to /etc/hosts
    local add_cmd="echo '${ip_address} ${fqdn}' >> /etc/hosts"
    if execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${satellite_user}" "${satellite_host}" \
                               "${add_cmd}" "true" > /dev/null 2>&1; then
        log_success "Added entry to /etc/hosts: ${ip_address} ${fqdn}"
    else
        log_error "Failed to add entry to /etc/hosts"
        return 1
    fi

    # Verify DNS resolution after modification
    local new_resolved_ip
    new_resolved_ip=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                             "${satellite_user}" "${satellite_host}" \
                                             "getent hosts ${fqdn} | awk '{print \$1}' | head -1" "false")

    if [[ "${new_resolved_ip}" == "${ip_address}" ]]; then
        log_success "DNS resolution verified: ${fqdn} -> ${new_resolved_ip}"
        return 0
    else
        log_error "DNS resolution still incorrect after modification: ${fqdn} -> ${new_resolved_ip}"
        return 1
    fi
}

# Check Red Hat Registry authentication
check_registry_auth() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Checking Red Hat Registry authentication"

    local authfile="/etc/foreman/registry-auth.json"

    # Check if auth file exists
    if ! execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                 "${satellite_user}" "${satellite_host}" \
                                 "test -f ${authfile}" "true" > /dev/null 2>&1; then
        log_info "Registry auth file does not exist: ${authfile}"
        echo "not_configured"
        return 1
    fi

    # Test authentication by trying to login (this validates existing credentials)
    if execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${satellite_user}" "${satellite_host}" \
                               "sudo -n podman login --authfile ${authfile} registry.redhat.io --get-login" "false" > /dev/null 2>&1; then
        log_success "Valid Red Hat Registry authentication found"
        echo "valid"
        return 0
    else
        log_warn "Registry auth file exists but authentication may be invalid"
        echo "invalid"
        return 1
    fi
}

# Configure Red Hat Registry authentication
configure_registry_auth() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Configuring Red Hat Registry authentication"

    if is_dry_run; then
        log_info "[DRY-RUN] Would configure Red Hat Registry authentication"
        return 0
    fi

    local authfile="/etc/foreman/registry-auth.json"

    echo ""
    log_info "Red Hat Registry authentication required"
    log_info "You can use either:"
    log_info "  1. Red Hat account username and password"
    log_info "  2. Registry Service Account token"

    local username
    local password

    username=$(prompt_input "Enter Red Hat Registry username or token name" "" "false")
    password=$(prompt_input "Enter password or token" "" "true")

    if [[ -z "${username}" || -z "${password}" ]]; then
        log_error "Username and password/token are required"
        return 1
    fi

    # Create authfile directory if it doesn't exist
    execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                           "${satellite_user}" "${satellite_host}" \
                           "mkdir -p /etc/foreman" "true" > /dev/null 2>&1

    # Login to registry (this creates/updates the auth file)
    local login_cmd="echo '${password}' | sudo -n podman login --authfile ${authfile} --username '${username}' --password-stdin registry.redhat.io"

    if execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${satellite_user}" "${satellite_host}" \
                               "${login_cmd}" "false" > /dev/null 2>&1; then
        log_success "Red Hat Registry authentication configured"

        # Set proper permissions
        execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${satellite_user}" "${satellite_host}" \
                               "chmod 600 ${authfile}" "true" > /dev/null 2>&1

        return 0
    else
        log_error "Failed to authenticate with Red Hat Registry"
        return 1
    fi
}

# Test image pull capability
test_image_pull() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Testing image pull capability"

    local authfile="/etc/foreman/registry-auth.json"
    local test_image="registry.redhat.io/satellite/iop-vulnerability-engine-rhel9:6.18"

    if is_dry_run; then
        log_info "[DRY-RUN] Would test pulling ${test_image}"
        return 0
    fi

    if execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${satellite_user}" "${satellite_host}" \
                               "sudo -n podman pull --authfile ${authfile} ${test_image}" "false" > /dev/null 2>&1; then
        log_success "Successfully pulled test image"

        # Clean up test image
        execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${satellite_user}" "${satellite_host}" \
                               "sudo -n podman rmi ${test_image}" "false" > /dev/null 2>&1

        return 0
    else
        log_error "Failed to pull test image"
        return 1
    fi
}

# Check if IOP is fully installed and running
is_iop_fully_installed() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    # Check if IOP packages are installed
    local iop_packages
    iop_packages=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                          "${satellite_user}" "${satellite_host}" \
                                          "rpm -qa | grep -E 'foreman-iop|satellite-iop' | wc -l" "false")

    if [[ "${iop_packages}" -eq 0 ]]; then
        echo "false"
        return 1
    fi

    # Check if IOP containers are running
    local container_count
    container_count=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                             "${satellite_user}" "${satellite_host}" \
                                             "sudo -n podman ps --filter 'label=com.redhat.component' --format '{{.Names}}' | grep -c iop || echo 0" "false")

    # Expect at least some containers running (the reference mentions 19 containers)
    if [[ "${container_count}" -ge 15 ]]; then
        echo "true"
        return 0
    else
        echo "false"
        return 1
    fi
}

# Install Lightspeed on-premises
install_lightspeed() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Installing Lightspeed on-premises"

    if is_dry_run; then
        log_info "[DRY-RUN] Would run: satellite-installer --enable-iop"
        return 0
    fi

    # Check if already fully installed
    if [[ "$(is_iop_fully_installed "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                     "${satellite_user}" "${satellite_host}")" == "true" ]]; then
        log_success "Lightspeed on-premises is already installed and running"
        return 0
    fi

    # Warn about impact
    log_warn "Installing Lightspeed on-premises will modify Satellite configuration"
    log_warn "This may temporarily affect Satellite services"

    if ! is_dry_run; then
        if ! confirm_action "Do you want to proceed with the installation?"; then
            log_info "Installation cancelled by user"
            return 1
        fi
    fi

    # Run satellite-installer
    log_info "Running satellite-installer --enable-iop (this may take several minutes)..."

    local install_output
    if ! install_output=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                                  "${satellite_user}" "${satellite_host}" \
                                                  "sudo -n satellite-installer --enable-iop 2>&1" "false"); then
        log_error "satellite-installer failed"
        log_error "Output: $(sanitize_output "${install_output}")"
        return 1
    fi

    log_success "satellite-installer completed"

    # Check installation result
    sleep 10  # Wait for services to stabilize

    local container_count
    container_count=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                             "${satellite_user}" "${satellite_host}" \
                                             "sudo -n podman ps --filter 'label=com.redhat.component' --format '{{.Names}}' | grep -c iop || echo 0" "false")

    if [[ "${container_count}" -ge 15 ]]; then
        log_success "IOP containers are running (${container_count} containers)"
    else
        log_warn "Expected more IOP containers, found ${container_count}"
    fi

    # Check for failed services
    local failed_services
    failed_services=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                             "${satellite_user}" "${satellite_host}" \
                                             "systemctl --failed --no-legend | grep -c . || echo 0" "true")

    if [[ "${failed_services}" -gt 0 ]]; then
        log_warn "Found ${failed_services} failed service(s)"
        execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${satellite_user}" "${satellite_host}" \
                               "systemctl --failed" "true" || true
    else
        log_success "No failed services detected"
    fi

    return 0
}

# Main setup function
run_satellite_setup() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_info "=== Starting Satellite Setup ==="

    # Check and fix DNS if needed
    local dns_status
    if dns_status=$(check_dns_mismatch "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                       "${satellite_user}" "${satellite_host}"); then
        log_success "DNS configuration is correct"
    else
        if [[ "${dns_status}" =~ ^mismatch:(.+):(.+):(.+)$ ]]; then
            local fqdn="${BASH_REMATCH[1]}"
            local primary_ip="${BASH_REMATCH[2]}"

            log_warn "DNS mismatch detected, fixing..."
            if ! fix_dns_configuration "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                       "${satellite_user}" "${satellite_host}" \
                                       "${fqdn}" "${primary_ip}"; then
                log_error "Failed to fix DNS configuration"
                return 1
            fi
        fi
    fi

    # Check and configure registry authentication
    local auth_status
    auth_status=$(check_registry_auth "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                      "${satellite_user}" "${satellite_host}" || echo "not_configured")

    if [[ "${auth_status}" != "valid" ]]; then
        if ! configure_registry_auth "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                     "${satellite_user}" "${satellite_host}"; then
            log_error "Failed to configure registry authentication"
            return 1
        fi
    fi

    # Test image pull
    if ! test_image_pull "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                         "${satellite_user}" "${satellite_host}"; then
        log_error "Failed to pull test image"
        return 1
    fi

    # Install Lightspeed
    if ! install_lightspeed "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                            "${satellite_user}" "${satellite_host}"; then
        log_error "Failed to install Lightspeed on-premises"
        return 1
    fi

    log_info "=== Satellite Setup Complete ==="
    log_success "Satellite is ready for Lightspeed on-premises"

    return 0
}

# If script is executed directly (not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [[ $# -lt 5 ]]; then
        echo "Usage: $0 <bastion_user> <bastion_host> <bastion_port> <satellite_user> <satellite_host>" >&2
        exit 1
    fi

    run_satellite_setup "$@"
fi
