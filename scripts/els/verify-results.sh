#!/bin/bash
# ELS CVE verification functions

verify_els_cves() {
    log_info "=== Verifying ELS CVE Detection ==="

    local output_dir="${SCRIPT_DIR}/output"
    local els_file="${output_dir}/els-cves-$(date +%Y%m%d_%H%M%S).json"

    if is_dry_run; then
        log_info "[DRY-RUN] Would verify ELS CVE detection"
        return 0
    fi

    # Create output directory
    mkdir -p "${output_dir}"

    # Use existing satellite_vulnerability.py
    if [[ ! -f "${SCRIPT_DIR}/satellite_vulnerability.py" ]]; then
        log_error "satellite_vulnerability.py not found"
        return 1
    fi

    log_info "Fetching ELS CVE data..."
    log_info "Output file: ${els_file}"

    # Execute satellite_vulnerability.py (fetch all CVEs for accurate comparison)
    if python3 "${SCRIPT_DIR}/satellite_vulnerability.py" \
        --content-view "${ELS_CONTENT_VIEW}" \
        --lifecycle-environment "${LIFECYCLE_ENV}" \
        --output "${els_file}"; then

        log_success "ELS CVE data captured successfully"

        # Validate data completeness
        if command -v jq &>/dev/null; then
            local is_complete
            is_complete=$(jq -r '.cve_list.complete // "unknown"' "${els_file}" 2>/dev/null)

            if [[ "${is_complete}" == "false" ]]; then
                log_error "ELS CVE data is incomplete (complete=false)"
                log_error "API may have failed or returned partial data"
                return 1
            elif [[ "${is_complete}" == "unknown" ]]; then
                log_warning "Could not verify data completeness (complete field missing)"
            fi

            # Display summary
            local cve_count
            cve_count=$(jq '.cves | length' "${els_file}" 2>/dev/null || echo "unknown")
            local total_cves
            total_cves=$(jq '.cve_list.total_items' "${els_file}" 2>/dev/null || echo "unknown")

            # Find latest CVE public date
            local latest_date
            latest_date=$(jq -r '[.cves[].public_date] | max' "${els_file}" 2>/dev/null || echo "unknown")

            # Count 2026 CVEs
            local cves_2026
            cves_2026=$(jq '[.cves[] | select(.public_date >= "2026-01-01")] | length' "${els_file}" 2>/dev/null || echo "unknown")

            log_info "ELS Verification Summary:"
            log_info "  CVEs in report: ${cve_count}"
            log_info "  Total CVEs in Satellite: ${total_cves}"
            log_info "  Latest CVE public date: ${latest_date}"
            log_info "  CVEs from 2026: ${cves_2026}"
            log_info "  Data complete: ${is_complete}"
            log_info "  ELS CVE file: ${els_file}"

            if [[ "${cves_2026}" != "0" ]] && [[ "${cves_2026}" != "unknown" ]]; then
                log_success "ELS CVEs detected successfully (${cves_2026} CVEs from 2026+)"
            else
                log_warning "No 2026+ CVEs detected - may need more time for evaluation"
            fi
        fi
    else
        log_error "Failed to capture ELS CVE data"
        return 1
    fi

    # Compare with baseline if available
    local latest_baseline
    latest_baseline=$(ls -t "${output_dir}"/baseline-cves-*.json 2>/dev/null | head -1)

    if [[ -n "${latest_baseline}" ]] && command -v jq &>/dev/null; then
        log_info "Comparing with baseline..."

        local baseline_count
        baseline_count=$(jq '.cve_list.total_items' "${latest_baseline}" 2>/dev/null || echo "unknown")
        local els_count
        els_count=$(jq '.cve_list.total_items' "${els_file}" 2>/dev/null || echo "unknown")

        if [[ "${baseline_count}" != "unknown" ]] && [[ "${els_count}" != "unknown" ]]; then
            local diff=$((els_count - baseline_count))
            log_info "CVE count difference: ${diff} (Baseline: ${baseline_count} -> ELS: ${els_count})"

            if [[ ${diff} -gt 0 ]]; then
                log_success "Detected ${diff} additional CVEs after ELS enablement"
            fi
        fi
    fi

    return 0
}
