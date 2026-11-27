#!/bin/bash
# Grouper optimization control functions

# Verify Grouper optimization disable is active
verify_grouper_optimization() {
    log_info "=== Verifying Grouper Optimization Disable ==="

    if is_dry_run; then
        log_info "[DRY-RUN] Would verify DISABLE_OPTIMISATION=True"
        return 0
    fi

    # Check if environment variable is set in container
    local container_check_cmd="podman exec iop-service-vuln-grouper printenv DISABLE_OPTIMISATION 2>/dev/null"
    local container_env
    container_env=$(satellite_exec "${container_check_cmd}" "Check DISABLE_OPTIMISATION" 2>/dev/null || echo "")

    if [[ "${container_env}" == *"True"* ]]; then
        log_success "DISABLE_OPTIMISATION=True confirmed in Grouper container"
        return 0
    else
        log_error "DISABLE_OPTIMISATION is not set to True in Grouper container"
        log_error "Current value: ${container_env:-not set}"
        log_error "ELS CVE detection may not work correctly without this setting"
        return 1
    fi
}

setup_grouper_optimization() {
    log_info "=== Setting up Grouper Optimization Disable (Demo-specific) ==="

    log_warning "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_warning "DEMO-SPECIFIC CONFIGURATION WARNING"
    log_warning "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_warning "This configuration disables Grouper optimization logic"
    log_warning "This is ONLY for demonstration purposes"
    log_warning "NOT recommended for production environments"
    log_warning "Use --rollback-grouper to restore normal operation after demo"
    log_warning "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    local drop_in_dir="/etc/containers/systemd/iop-service-vuln-grouper.container.d"
    local drop_in_file="${drop_in_dir}/99-demo-disable-optimisation.conf"

    # Check if already configured
    local check_cmd="test -f ${drop_in_file}"
    if satellite_exec "${check_cmd}" "Check drop-in file" >/dev/null 2>&1; then
        log_success "Grouper optimization disable already configured"
        return 0
    fi

    if is_dry_run; then
        log_info "[DRY-RUN] Would create Quadlet drop-in configuration"
        log_info "[DRY-RUN] File: ${drop_in_file}"
        log_info "[DRY-RUN] Would set: DISABLE_OPTIMISATION=True"
        return 0
    fi

    # Create drop-in directory
    satellite_exec "mkdir -p ${drop_in_dir}" "Create drop-in directory"

    # Create drop-in configuration
    log_info "Creating Quadlet drop-in configuration..."

    # Use printf to create the file with proper escaping
    satellite_exec "printf '[Container]\\nEnvironment=DISABLE_OPTIMISATION=True\\n' > ${drop_in_file}" "Create drop-in file"

    # Verify file was created
    if ! satellite_exec "test -f ${drop_in_file}" "Verify drop-in file created" >/dev/null 2>&1; then
        log_error "Failed to create drop-in file"
        return 1
    fi

    log_success "Drop-in file created successfully"

    # Reload systemd
    log_info "Reloading systemd daemon..."
    satellite_exec "systemctl daemon-reload" "Reload systemd"

    # Verify configuration in generated systemd service
    log_info "Verifying systemd service configuration..."
    local verify_cmd="systemctl cat iop-service-vuln-grouper.service | grep -q 'DISABLE_OPTIMISATION=True'"
    if satellite_exec "${verify_cmd}" "Verify environment variable"; then
        log_success "Configuration verified in systemd service"
    else
        log_error "Configuration not found in systemd service"
        return 1
    fi

    # Restart Grouper service
    log_info "Restarting Grouper service..."
    satellite_exec "systemctl restart iop-service-vuln-grouper.service" "Restart Grouper"

    # Wait for service to be active
    sleep 5

    if check_service_active "iop-service-vuln-grouper.service"; then
        log_success "Grouper service restarted successfully"
    else
        log_error "Grouper service failed to start"
        return 1
    fi

    # Verify environment variable in container
    log_info "Verifying environment variable in container..."
    local container_check_cmd="podman exec iop-service-vuln-grouper printenv DISABLE_OPTIMISATION"
    local container_env
    container_env=$(satellite_exec "${container_check_cmd}" "Check container environment")

    if [[ "${container_env}" == *"True"* ]]; then
        log_success "DISABLE_OPTIMISATION=True confirmed in container"
    else
        log_error "DISABLE_OPTIMISATION not set correctly in container"
        return 1
    fi

    log_success "Grouper optimization disable configured successfully"
    return 0
}

rollback_grouper_optimization() {
    log_info "=== Rolling back Grouper Optimization Settings ==="

    local drop_in_dir="/etc/containers/systemd/iop-service-vuln-grouper.container.d"
    local drop_in_file="${drop_in_dir}/99-demo-disable-optimisation.conf"

    # Check if configuration exists
    local check_cmd="test -f ${drop_in_file}"
    if ! satellite_exec "${check_cmd}" "Check drop-in file" >/dev/null 2>&1; then
        log_info "Drop-in configuration not found (already removed or never created)"
        return 0
    fi

    if is_dry_run; then
        log_info "[DRY-RUN] Would remove: ${drop_in_file}"
        log_info "[DRY-RUN] Would reload systemd daemon"
        log_info "[DRY-RUN] Would restart Grouper service"
        return 0
    fi

    # Remove drop-in file
    log_info "Removing drop-in configuration..."
    satellite_exec "rm -f ${drop_in_file}" "Remove drop-in file"

    # Reload systemd
    log_info "Reloading systemd daemon..."
    satellite_exec "systemctl daemon-reload" "Reload systemd"

    # Restart Grouper service
    log_info "Restarting Grouper service..."
    satellite_exec "systemctl restart iop-service-vuln-grouper.service" "Restart Grouper"

    # Wait for service to be active
    sleep 5

    if check_service_active "iop-service-vuln-grouper.service"; then
        log_success "Grouper service restarted successfully"
    else
        log_error "Grouper service failed to start"
        return 1
    fi

    # Verify environment variable is not set
    log_info "Verifying environment variable removal..."
    local container_check_cmd="podman exec iop-service-vuln-grouper printenv DISABLE_OPTIMISATION 2>/dev/null || echo 'not set'"
    local container_env
    container_env=$(satellite_exec "${container_check_cmd}" "Check container environment")

    if [[ "${container_env}" == *"not set"* ]] || [[ -z "${container_env}" ]]; then
        log_success "DISABLE_OPTIMISATION removed from container"
    else
        log_warning "DISABLE_OPTIMISATION may still be set: ${container_env}"
    fi

    log_success "Grouper optimization settings rolled back successfully"
    return 0
}
