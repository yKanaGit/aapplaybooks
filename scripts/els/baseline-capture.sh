#!/bin/bash
# Baseline CVE capture functions

# Trigger Insights re-upload from all nodes for baseline
trigger_insights_for_baseline() {
    log_info "=== Triggering Insights data for baseline ==="

    local nodes=("${NODE1_HOST:-node1.example.com}" "${NODE2_HOST:-node2.example.com}" "${NODE3_HOST}")

    for node in "${nodes[@]}"; do
        log_info "Sending Insights data from ${node}..."

        if is_dry_run; then
            log_info "[DRY-RUN] Would send Insights data from ${node}"
        else
            node_exec "${node}" "insights-client" "Send Insights data from ${node}"
        fi
    done

    # Wait for Insights data processing and vulnerability evaluation
    if ! is_dry_run; then
        log_info "Waiting for Insights data processing and vulnerability evaluation..."
        log_info "This may take 5-10 minutes for Evaluator and Grouper to process..."
        sleep 300  # Minimum 5 minutes wait
        log_success "Insights evaluation wait period completed"
    fi

    return 0
}

capture_baseline() {
    log_info "=== Capturing Baseline CVE Data ==="

    local output_dir="${SCRIPT_DIR}/output"
    local baseline_file="${output_dir}/baseline-cves-$(date +%Y%m%d_%H%M%S).json"

    if is_dry_run; then
        log_info "[DRY-RUN] Would capture baseline CVE data to: ${baseline_file}"
        return 0
    fi

    # Create output directory
    mkdir -p "${output_dir}"

    # Use existing satellite_vulnerability.py
    if [[ ! -f "${SCRIPT_DIR}/satellite_vulnerability.py" ]]; then
        log_error "satellite_vulnerability.py not found"
        return 1
    fi

    log_info "Fetching baseline CVE data..."
    log_info "Output file: ${baseline_file}"

    # Execute satellite_vulnerability.py (fetch all CVEs for accurate baseline)
    # Use --top 0 to fetch all CVEs (not limited to top N)
    if python3 "${SCRIPT_DIR}/satellite_vulnerability.py" \
        --top 0 \
        --lifecycle-environment "${LIFECYCLE_ENV}" \
        --output "${baseline_file}"; then

        log_success "Baseline CVE data captured successfully"

        # Validate data completeness
        if ! command -v jq &>/dev/null; then
            log_error "jq is required but not installed"
            return 1
        fi

        # Check if complete field is true
        if ! jq -e '.complete == true' "${baseline_file}" >/dev/null 2>&1; then
            local complete_value
            complete_value=$(jq -r '.complete // "missing"' "${baseline_file}" 2>/dev/null)
            log_error "Baseline CVE data is incomplete"
            log_error "  complete field: ${complete_value}"
            log_error "API may have failed or returned partial data"
            return 1
        fi

        # Display summary
        local cve_count
        cve_count=$(jq '.cves | length' "${baseline_file}" 2>/dev/null || echo "unknown")
        local fetched
        fetched=$(jq '.cve_list.fetched // "unknown"' "${baseline_file}" 2>/dev/null)

        log_info "Summary:"
        log_info "  CVEs in report: ${cve_count}"
        log_info "  CVEs fetched from API: ${fetched}"
        log_info "  Data complete: true"
        log_info "  Baseline file: ${baseline_file}"
    else
        log_error "Failed to capture baseline CVE data"
        return 1
    fi

    return 0
}
