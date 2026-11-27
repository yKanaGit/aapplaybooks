#!/bin/bash
# Satellite IoP setup functions

setup_satellite_iop() {
    log_info "=== Setting up Satellite IoP ==="

    # Check if IoP is already enabled
    log_info "Checking IoP status..."

    if satellite_exec "command -v iop-cvemap-download" "Check IoP installation"; then
        log_success "IoP is already installed"
    else
        log_info "Enabling IoP on Satellite..."

        if is_dry_run; then
            log_info "[DRY-RUN] Would run: satellite-installer --enable-iop"
        else
            satellite_exec "satellite-installer --enable-iop" "Enable IoP"
            if [[ $? -ne 0 ]]; then
                log_error "Failed to enable IoP"
                return 1
            fi
        fi
    fi

    # Check IoP services
    log_info "Checking IoP services..."

    local iop_services=(
        "iop-service-vuln-grouper.service"
        "iop-service-vuln-evaluator.service"
        "iop-service-vuln-manager.service"
        "iop-service-vuln-listener.service"
        "iop-service-vuln-vmaas-sync.service"
        "iop-service-vmaas-webapp-go.service"
        "iop-service-vmaas-reposcan.service"
    )

    local all_active=true
    for service in "${iop_services[@]}"; do
        if check_service_active "${service}"; then
            log_success "${service} is active"
        else
            log_warning "${service} is not active"
            all_active=false
        fi
    done

    if [[ "${all_active}" == "false" ]]; then
        log_error "Some IoP services are not active"
        return 1
    fi

    log_success "All IoP services are active"
    return 0
}
