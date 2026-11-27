#!/bin/bash
# Insights Client setup for Lightspeed on-premises

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lightspeed/common.sh
source "${SCRIPT_DIR}/common.sh"

# Set Satellite host parameter for Insights registration
set_satellite_host_parameter() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"
    local target_hostname="$6"

    log_step "Setting Satellite host parameter for Insights registration"

    if is_dry_run; then
        log_info "[DRY-RUN] Would set host_registration_insights=true for ${target_hostname}"
        return 0
    fi

    # Try using Hammer CLI first
    local hammer_check
    if hammer_check=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                             "${satellite_user}" "${satellite_host}" \
                                             "hammer --version" "false" 2>&1); then
        log_info "Using Hammer CLI to set host parameter"

        # Check current parameter value
        local current_value
        current_value=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                               "${satellite_user}" "${satellite_host}" \
                                               "hammer host info --name ${target_hostname} --fields Parameters 2>/dev/null | grep host_registration_insights | awk '{print \$2}' || echo 'not_set'" "false" || echo "not_set")

        log_info "Current host_registration_insights value: ${current_value}"

        if [[ "${current_value}" == "true" ]]; then
            log_success "Host parameter already set to true"
            return 0
        fi

        # Set the parameter
        if execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                   "${satellite_user}" "${satellite_host}" \
                                   "hammer host set-parameter --host ${target_hostname} --name host_registration_insights --parameter-type boolean --value true" "false" > /dev/null 2>&1; then
            log_success "Host parameter set successfully via Hammer CLI"
            return 0
        else
            log_warn "Failed to set parameter via Hammer CLI, trying alternative method"
        fi
    fi

    # Alternative: provide manual instructions
    log_warn "Automatic parameter setting failed"
    log_info "Please manually set the following in Satellite UI:"
    log_info "  Host: ${target_hostname}"
    log_info "  Parameter: host_registration_insights"
    log_info "  Type: boolean"
    log_info "  Value: true"

    if ! is_dry_run; then
        if ! confirm_action "Have you set the parameter manually?"; then
            log_error "Host parameter not set"
            return 1
        fi
    fi

    return 0
}

# Check node1 RHEL version and registration status
check_node1_registration() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local node1_user="$4"
    local node1_host="$5"

    log_step "Checking node1 registration status"

    # Check RHEL version
    local rhel_version
    rhel_version=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                          "${node1_user}" "${node1_host}" \
                                          "cat /etc/redhat-release" "false")
    log_info "RHEL version: ${rhel_version}"

    # Check subscription-manager registration
    local sm_identity
    sm_identity=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                         "${node1_user}" "${node1_host}" \
                                         "subscription-manager identity 2>&1 || echo 'not_registered'" "true")

    if [[ "${sm_identity}" =~ not_registered ]]; then
        log_error "node1 is not registered to Subscription Manager"
        return 1
    fi

    log_info "Subscription Manager identity:"
    echo "${sm_identity}" | while IFS= read -r line; do
        log_info "  ${line}"
    done

    # Check subscription-manager config
    local sm_config
    sm_config=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                       "${node1_user}" "${node1_host}" \
                                       "subscription-manager config | grep -E 'hostname|baseurl'" "true")
    log_info "Subscription Manager config:"
    echo "${sm_config}" | while IFS= read -r line; do
        log_info "  ${line}"
    done

    return 0
}

# Install insights-client if not present
install_insights_client() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local node1_user="$4"
    local node1_host="$5"

    log_step "Checking insights-client installation"

    # Check if insights-client is installed
    if execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${node1_user}" "${node1_host}" \
                               "rpm -q insights-client" "false" > /dev/null 2>&1; then
        local version
        version=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                         "${node1_user}" "${node1_host}" \
                                         "rpm -q insights-client" "false")
        log_success "insights-client already installed: ${version}"
        return 0
    fi

    if is_dry_run; then
        log_info "[DRY-RUN] Would install insights-client"
        return 0
    fi

    # Install insights-client
    log_info "Installing insights-client..."

    if execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${node1_user}" "${node1_host}" \
                               "yum install -y insights-client" "true" > /dev/null 2>&1; then
        local version
        version=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                         "${node1_user}" "${node1_host}" \
                                         "rpm -q insights-client" "false")
        log_success "insights-client installed: ${version}"
        return 0
    else
        log_error "Failed to install insights-client"
        return 1
    fi
}

# Check insights-client registration status
check_insights_registration() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local node1_user="$4"
    local node1_host="$5"
    local expected_satellite_fqdn="$6"

    log_step "Checking insights-client registration status"

    # Check registration status
    local insights_status
    insights_status=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                             "${node1_user}" "${node1_host}" \
                                             "insights-client --status 2>&1 || echo 'not_registered'" "true")

    log_info "Insights status:"
    echo "${insights_status}" | while IFS= read -r line; do
        log_info "  ${line}"
    done

    # Check if registered
    if [[ "${insights_status}" =~ "not registered" ]] || [[ "${insights_status}" =~ "not_registered" ]]; then
        log_info "insights-client is not registered"
        echo "not_registered"
        return 1
    fi

    # Check if registered to correct Satellite
    if [[ -n "${expected_satellite_fqdn}" ]]; then
        if echo "${insights_status}" | grep -qi "${expected_satellite_fqdn}"; then
            log_success "insights-client is registered to ${expected_satellite_fqdn}"
            echo "registered"
            return 0
        else
            log_warn "insights-client may be registered to a different server"
            echo "wrong_server"
            return 1
        fi
    fi

    log_success "insights-client is registered"
    echo "registered"
    return 0
}

# Unregister insights-client
unregister_insights_client() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local node1_user="$4"
    local node1_host="$5"

    log_step "Unregistering insights-client"

    if is_dry_run; then
        log_info "[DRY-RUN] Would unregister insights-client"
        return 0
    fi

    if execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                               "${node1_user}" "${node1_host}" \
                               "insights-client --unregister" "true" > /dev/null 2>&1; then
        log_success "insights-client unregistered"
        return 0
    else
        log_warn "Failed to unregister insights-client (may not be registered)"
        return 0  # Not a critical error
    fi
}

# Register insights-client
register_insights_client() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local node1_user="$4"
    local node1_host="$5"

    log_step "Registering insights-client"

    if is_dry_run; then
        log_info "[DRY-RUN] Would register insights-client"
        return 0
    fi

    local register_output
    if ! register_output=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                                   "${node1_user}" "${node1_host}" \
                                                   "insights-client --register 2>&1" "true"); then
        # Check for specific error messages
        if echo "${register_output}" | grep -qi "403.*Forbidden"; then
            log_error "Registration failed with 403 Forbidden"
            log_error "Please verify that host_registration_insights is set to true in Satellite"
            return 1
        elif echo "${register_output}" | grep -qi "machine-id.*found"; then
            log_warn "Machine-id conflict detected, unregistering first..."
            unregister_insights_client "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                       "${node1_user}" "${node1_host}"

            # Retry registration
            if ! register_output=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                                           "${node1_user}" "${node1_host}" \
                                                           "insights-client --register 2>&1" "true"); then
                log_error "Registration failed after unregister"
                log_error "Output: ${register_output}"
                return 1
            fi
        else
            log_error "Registration failed"
            log_error "Output: ${register_output}"
            return 1
        fi
    fi

    log_success "insights-client registered successfully"
    return 0
}

# Upload system information
upload_insights_data() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local node1_user="$4"
    local node1_host="$5"

    log_step "Uploading system information to Insights"

    if is_dry_run; then
        log_info "[DRY-RUN] Would upload system information"
        return 0
    fi

    local upload_output
    if upload_output=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                              "${node1_user}" "${node1_host}" \
                                              "insights-client 2>&1" "true"); then
        log_success "System information uploaded successfully"
        log_info "Upload result:"
        echo "${upload_output}" | tail -10 | while IFS= read -r line; do
            log_info "  ${line}"
        done
        return 0
    else
        log_error "Failed to upload system information"
        log_error "Output: ${upload_output}"
        return 1
    fi
}

# Main insights client setup function
run_insights_client_setup() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"
    local node1_user="$6"
    local node1_host="$7"
    local node1_hostname="$8"

    log_info "=== Starting Insights Client Setup ==="

    # Get Satellite FQDN
    local satellite_fqdn
    satellite_fqdn=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                            "${satellite_user}" "${satellite_host}" \
                                            "hostname -f" "false")

    log_info "Target Satellite: ${satellite_fqdn}"
    log_info "Target node1: ${node1_hostname}"

    # Set Satellite host parameter
    if ! set_satellite_host_parameter "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                      "${satellite_user}" "${satellite_host}" \
                                      "${node1_hostname}"; then
        log_error "Failed to set Satellite host parameter"
        return 1
    fi

    # Check node1 registration
    if ! check_node1_registration "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                  "${node1_user}" "${node1_host}"; then
        log_error "node1 is not properly registered to Satellite"
        return 1
    fi

    # Install insights-client if needed
    if ! install_insights_client "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                 "${node1_user}" "${node1_host}"; then
        log_error "Failed to install insights-client"
        return 1
    fi

    # Check insights registration status
    local reg_status
    reg_status=$(check_insights_registration "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                             "${node1_user}" "${node1_host}" \
                                             "${satellite_fqdn}" || echo "not_registered")

    # Register if needed
    if [[ "${reg_status}" != "registered" ]]; then
        if [[ "${reg_status}" == "wrong_server" ]]; then
            log_info "Re-registering to correct Satellite..."
            unregister_insights_client "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                       "${node1_user}" "${node1_host}"
        fi

        if ! register_insights_client "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                      "${node1_user}" "${node1_host}"; then
            log_error "Failed to register insights-client"
            return 1
        fi
    fi

    # Upload system information
    if ! upload_insights_data "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                              "${node1_user}" "${node1_host}"; then
        log_warn "Failed to upload system information (registration may still be valid)"
    fi

    # Verify final status
    check_insights_registration "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                "${node1_user}" "${node1_host}" \
                                "${satellite_fqdn}"

    log_info "=== Insights Client Setup Complete ==="
    log_success "node1 is configured for Insights on-premises"

    return 0
}

# If script is executed directly (not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [[ $# -lt 8 ]]; then
        echo "Usage: $0 <bastion_user> <bastion_host> <bastion_port> <satellite_user> <satellite_host> <node1_user> <node1_host> <node1_hostname>" >&2
        exit 1
    fi

    run_insights_client_setup "$@"
fi
