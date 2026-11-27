#!/bin/bash
# Node3 setup functions

# Enable ELS repository on node3 (without Insights data send)
enable_els_repository_on_node3() {
    log_info "=== Enabling ELS repository on node3 ==="

    # Refresh subscription
    log_info "Refreshing subscription on node3..."
    node_exec "${NODE3_HOST}" "subscription-manager refresh" "Refresh subscription"

    # Enable ELS repository
    log_info "Enabling ELS repository on node3..."

    if is_dry_run; then
        log_info "[DRY-RUN] Would enable rhel-7-server-els-rpms repository"
    else
        node_exec "${NODE3_HOST}" "subscription-manager repos --enable=rhel-7-server-els-rpms" \
            "Enable ELS repository"
    fi

    # Verify repository list
    log_info "Verifying repository list..."
    node_exec "${NODE3_HOST}" "yum repolist | grep -i els" "Check ELS repository"

    # Check available ELS security updates
    log_info "Checking ELS security updates..."
    node_exec "${NODE3_HOST}" "yum --disablerepo='*' --enablerepo=rhel-7-server-els-rpms updateinfo list updates security | head -20" \
        "List ELS security updates"

    log_success "ELS repository enabled on node3 successfully"
    return 0
}

# Send Insights data and wait for vulnerability evaluation
send_insights_and_wait_evaluation() {
    log_info "=== Sending Insights data and waiting for evaluation ==="

    # Send Insights data
    log_info "Sending Insights data from node3..."

    if is_dry_run; then
        log_info "[DRY-RUN] Would run: insights-client"
        log_info "[DRY-RUN] Would wait for vulnerability evaluation"
    else
        node_exec "${NODE3_HOST}" "insights-client" "Send Insights data"

        # Wait for Insights data to be received by Satellite
        log_info "Waiting for Insights data to be received by Satellite..."
        sleep 30

        # Wait for vulnerability evaluation to complete
        wait_for_vulnerability_evaluation "${NODE3_HOST}"
    fi

    log_success "Insights data sent and evaluation completed"
    return 0
}

# Wait for vulnerability evaluation to complete for a host
wait_for_vulnerability_evaluation() {
    local hostname="$1"
    local start_time
    start_time=$(date '+%Y-%m-%d %H:%M:%S')
    local max_wait=900  # 15 minutes
    local wait_interval=30
    local elapsed=0
    local min_wait=300  # Minimum 5 minutes

    log_info "Waiting for vulnerability evaluation to complete for ${hostname}..."
    log_info "Start time: ${start_time}"
    log_info "This may take 5-15 minutes as Evaluator and Grouper process the data..."

    while [[ ${elapsed} -lt ${max_wait} ]]; do
        # Check Evaluator logs for recent activity on this host
        local evaluator_active=false
        if satellite_exec "journalctl -u iop-service-vuln-evaluator.service --since '${start_time}' | grep -qE '${hostname}|Evaluated.*system'" "Check Evaluator activity" >/dev/null 2>&1; then
            log_info "Evaluator has processed data (detected activity in logs)"
            evaluator_active=true
        fi

        # Check Grouper logs for recent activity
        local grouper_active=false
        if satellite_exec "journalctl -u iop-service-vuln-grouper.service --since '${start_time}' | grep -qE '${hostname}|Grouped.*system|Processing.*complete'" "Check Grouper activity" >/dev/null 2>&1; then
            log_info "Grouper has processed data (detected activity in logs)"
            grouper_active=true
        fi

        # After minimum wait time, if we've seen activity, consider it complete
        if [[ ${elapsed} -ge ${min_wait} ]]; then
            if [[ "${evaluator_active}" == "true" ]] || [[ "${grouper_active}" == "true" ]]; then
                log_success "Vulnerability evaluation completed (detected processing activity and minimum wait elapsed)"
                return 0
            else
                log_info "Minimum wait time elapsed but no processing activity detected yet..."
            fi
        fi

        sleep ${wait_interval}
        elapsed=$((elapsed + wait_interval))
        log_info "Waiting for evaluation to complete... (${elapsed}/${max_wait}s, minimum: ${min_wait}s)"
    done

    log_warning "Evaluation wait timeout reached (${max_wait}s)"
    log_warning "Proceeding anyway - CVE detection may be incomplete"
    return 0
}

# Legacy function kept for compatibility (now calls the split functions)
enable_els_on_node3() {
    enable_els_repository_on_node3
    send_insights_and_wait_evaluation
}
