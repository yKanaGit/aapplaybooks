#!/bin/bash
# Insights Client setup functions for ELS demo nodes

setup_insights_client_all_nodes() {
    log_info "=== Setting up Insights Client on all nodes ==="

    local nodes=("${NODE1_HOST:-node1.example.com}" "${NODE2_HOST:-node2.example.com}" "${NODE3_HOST}")

    for node in "${nodes[@]}"; do
        setup_insights_client_on_node "${node}"
    done

    log_success "Insights Client setup completed on all nodes"
    return 0
}

setup_insights_client_on_node() {
    local node="$1"

    log_info "Setting up Insights Client on ${node}..."

    # Check if insights-client is installed
    if is_dry_run; then
        log_info "[DRY-RUN] Would check and install insights-client on ${node}"
    else
        # Check installation
        if ! node_exec "${node}" "rpm -q insights-client" "Check insights-client package" >/dev/null 2>&1; then
            log_info "Installing insights-client on ${node}..."
            node_exec "${node}" "yum install -y insights-client" "Install insights-client"
        else
            log_success "insights-client already installed on ${node}"
        fi
    fi

    # Configure for on-premises (Satellite)
    log_info "Configuring insights-client for on-premises on ${node}..."

    if is_dry_run; then
        log_info "[DRY-RUN] Would configure insights-client.conf on ${node}"
    else
        # Set base_url to point to Satellite
        local config_cmd="sed -i 's/^#*base_url.*/base_url=https:\\/\\/${SATELLITE_HOST}\\/redhat_access\\/r\\/insights/' /etc/insights-client/insights-client.conf"
        node_exec "${node}" "${config_cmd}" "Configure base_url"

        # Ensure auto_config is disabled (use manual config)
        local auto_config_cmd="grep -q '^auto_config' /etc/insights-client/insights-client.conf && sed -i 's/^auto_config.*/auto_config=False/' /etc/insights-client/insights-client.conf || echo 'auto_config=False' >> /etc/insights-client/insights-client.conf"
        node_exec "${node}" "${auto_config_cmd}" "Disable auto_config"
    fi

    # Register with Insights (via Satellite)
    log_info "Registering Insights Client on ${node}..."

    if is_dry_run; then
        log_info "[DRY-RUN] Would register insights-client on ${node}"
    else
        # Check if already registered
        if node_exec "${node}" "insights-client --status" "Check registration status" >/dev/null 2>&1; then
            log_success "insights-client already registered on ${node}"
        else
            log_info "Registering insights-client on ${node}..."
            node_exec "${node}" "insights-client --register" "Register insights-client"
        fi
    fi

    # Send initial data
    log_info "Sending initial Insights data from ${node}..."

    if is_dry_run; then
        log_info "[DRY-RUN] Would send Insights data from ${node}"
    else
        node_exec "${node}" "insights-client" "Send Insights data"
    fi

    log_success "Insights Client configured on ${node}"
    return 0
}
