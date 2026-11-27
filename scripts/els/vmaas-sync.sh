#!/bin/bash
# VMaaS synchronization functions

# Wait for oneshot service completion
wait_for_oneshot_service() {
    local service="$1"
    local description="$2"
    local max_wait="${3:-600}"
    local wait_interval="${4:-15}"

    log_info "Waiting for ${description} (timeout: ${max_wait}s)..."

    local elapsed=0
    while [[ ${elapsed} -lt ${max_wait} ]]; do
        # Check if service is inactive (oneshot services go inactive after completion)
        if ! check_service_active "${service}"; then
            # Check if it succeeded
            if satellite_exec "systemctl status ${service} | grep -qE 'code=exited, status=0/SUCCESS|Status:.*Succeeded'" "Check service status" >/dev/null 2>&1; then
                log_success "${description} completed successfully"
                return 0
            else
                log_error "${description} failed or did not complete successfully"
                satellite_exec "systemctl status ${service} | tail -20" "Show service status" || true
                return 1
            fi
        fi

        sleep ${wait_interval}
        elapsed=$((elapsed + wait_interval))
        log_info "${description} still running... (${elapsed}/${max_wait}s)"
    done

    log_error "Timeout waiting for ${description}"
    return 1
}

# Wait for VMaaS reposcan completion (long-running service)
wait_for_reposcan_completion() {
    local start_time
    start_time=$(date '+%Y-%m-%d %H:%M:%S')
    local max_wait=900  # 15 minutes
    local wait_interval=20
    local elapsed=0

    log_info "Waiting for VMaaS reposcan to complete..."
    log_info "Start time: ${start_time}"
    log_info "Looking for: 'Import repositories from Katello + Sync repositories + Sync CVE map task finished'"

    while [[ ${elapsed} -lt ${max_wait} ]]; do
        # Check for completion message in logs since start time
        if satellite_exec "journalctl -u iop-service-vmaas-reposcan.service --since '${start_time}' | grep -q 'Import repositories from Katello + Sync repositories + Sync CVE map task finished'" "Check reposcan completion" >/dev/null 2>&1; then
            log_success "VMaaS reposcan completed successfully"
            return 0
        fi

        # Check for errors
        if satellite_exec "journalctl -u iop-service-vmaas-reposcan.service --since '${start_time}' | grep -qE 'Error|Failed|Exception'" "Check reposcan errors" >/dev/null 2>&1; then
            log_error "VMaaS reposcan encountered errors"
            satellite_exec "journalctl -u iop-service-vmaas-reposcan.service --since '${start_time}' | tail -30" "Show recent logs" || true
            return 1
        fi

        sleep ${wait_interval}
        elapsed=$((elapsed + wait_interval))
        log_info "Waiting for reposcan to complete... (${elapsed}/${max_wait}s)"
    done

    log_error "Timeout waiting for VMaaS reposcan completion"
    satellite_exec "journalctl -u iop-service-vmaas-reposcan.service --since '${start_time}' | tail -50" "Show recent logs" || true
    return 1
}

# Check VMaaS Webapp data reload
check_webapp_data_reload() {
    log_info "Checking VMaaS Webapp data reload..."

    if is_dry_run; then
        log_info "[DRY-RUN] Would check Webapp data reload"
        return 0
    fi

    # Wait a bit for Webapp to detect new data
    sleep 15

    # Check Webapp logs for recent data load
    local log_check_cmd="journalctl -u iop-service-vmaas-webapp-go.service --since '2 minutes ago' | grep -qE 'Loading.*dump|Loaded.*packages|Cache.*refreshed' || journalctl -u iop-service-vmaas-webapp-go.service -n 100 | grep -qE 'Loading.*dump|Loaded.*packages|Cache.*refreshed'"

    if satellite_exec "${log_check_cmd}" "Check Webapp data load" >/dev/null 2>&1; then
        log_success "VMaaS Webapp data reload detected"
        return 0
    else
        log_warning "Could not confirm Webapp data reload in logs (may still be loading)"
        # Not a critical error, continue
        return 0
    fi
}

sync_vmaas_all() {
    log_info "=== Synchronizing VMaaS ==="

    # Step 1: Trigger CVE map download
    log_info "Step 1: Triggering CVE map download..."

    if is_dry_run; then
        log_info "[DRY-RUN] Would start iop-cvemap-download.service"
        log_info "[DRY-RUN] Would wait for reposcan completion"
        log_info "[DRY-RUN] Would check Webapp data reload"
        log_info "[DRY-RUN] Would start iop-service-vuln-vmaas-sync.service"
    else
        satellite_exec "systemctl start iop-cvemap-download.service" "Start CVE map download"

        # Wait for CVE map download to complete (oneshot service)
        if ! wait_for_oneshot_service "iop-cvemap-download.service" "CVE map download" 300 10; then
            log_error "CVE map download failed"
            return 1
        fi

        # Step 2: Wait for reposcan to process the data
        log_info "Step 2: Waiting for VMaaS reposcan to complete..."

        if ! wait_for_reposcan_completion; then
            log_error "VMaaS reposcan failed or timed out"
            return 1
        fi

        # Step 3: Check Webapp data reload
        log_info "Step 3: Checking VMaaS Webapp data reload..."
        check_webapp_data_reload

        # Step 4: Trigger VMaaS sync to Vulnerability Service
        log_info "Step 4: Triggering VMaaS sync to Vulnerability Service..."
        satellite_exec "systemctl start iop-service-vuln-vmaas-sync.service" "Start VMaaS sync"

        # Wait for sync to complete (oneshot service)
        if ! wait_for_oneshot_service "iop-service-vuln-vmaas-sync.service" "VMaaS sync to Vulnerability Service" 600 15; then
            log_error "VMaaS sync to Vulnerability Service failed"
            return 1
        fi
    fi

    log_success "VMaaS synchronization completed successfully"
    return 0
}
