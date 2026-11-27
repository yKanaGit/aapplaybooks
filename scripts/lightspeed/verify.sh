#!/bin/bash
# Verification script for Lightspeed on-premises setup

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lightspeed/common.sh
source "${SCRIPT_DIR}/common.sh"

# Initialize verification report
init_report() {
    cat <<'EOF'
{
  "timestamp": "",
  "satellite": {},
  "lightspeed": {},
  "vulnerability_sync": {},
  "insights_client": {},
  "issues": [],
  "warnings": [],
  "summary": {
    "status": "unknown",
    "total_checks": 0,
    "passed_checks": 0,
    "failed_checks": 0,
    "warning_checks": 0
  }
}
EOF
}

# Verify Satellite basic info
verify_satellite_info() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Verifying Satellite information"

    local satellite_version
    satellite_version=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                               "${satellite_user}" "${satellite_host}" \
                                               "rpm -q satellite --qf '%{VERSION}' 2>/dev/null || echo 'unknown'" "false")

    local os_version
    os_version=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                        "${satellite_user}" "${satellite_host}" \
                                        "cat /etc/redhat-release" "false")

    local fqdn
    fqdn=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                  "${satellite_user}" "${satellite_host}" \
                                  "hostname -f" "false")

    log_info "Satellite Version: ${satellite_version}"
    log_info "OS: ${os_version}"
    log_info "FQDN: ${fqdn}"

    echo "{\"version\":\"${satellite_version}\",\"os\":\"${os_version}\",\"fqdn\":\"${fqdn}\"}"
}

# Verify DNS configuration
verify_dns_config() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Verifying DNS configuration"

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

    if [[ "${resolved_ip}" == "${primary_ip}" ]]; then
        log_success "DNS configuration is correct"
        echo "{\"status\":\"ok\",\"fqdn\":\"${fqdn}\",\"ip\":\"${primary_ip}\"}"
        return 0
    else
        log_error "DNS mismatch: ${fqdn} resolves to ${resolved_ip} but NIC has ${primary_ip}"
        echo "{\"status\":\"mismatch\",\"fqdn\":\"${fqdn}\",\"nic_ip\":\"${primary_ip}\",\"resolved_ip\":\"${resolved_ip}\"}"
        return 1
    fi
}

# Verify IOP installation
verify_iop_installation() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Verifying IOP installation"

    local container_count
    container_count=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                             "${satellite_user}" "${satellite_host}" \
                                             "sudo -n podman ps --filter 'label=com.redhat.component' --format '{{.Names}}' | grep -c iop || echo 0" "false")

    local container_list
    container_list=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                            "${satellite_user}" "${satellite_host}" \
                                            "sudo -n podman ps --filter 'label=com.redhat.component' --format '{{.Names}}' | grep iop | sort | paste -sd ',' -" "false" || echo "")

    local failed_services
    failed_services=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                             "${satellite_user}" "${satellite_host}" \
                                             "systemctl --failed --no-legend | grep -c . || echo 0" "true")

    log_info "IOP containers running: ${container_count}"

    if [[ "${container_count}" -ge 15 ]]; then
        log_success "IOP is installed and running"
        echo "{\"status\":\"installed\",\"container_count\":${container_count},\"failed_services\":${failed_services}}"
        return 0
    elif [[ "${container_count}" -gt 0 ]]; then
        log_warn "IOP is partially installed (${container_count} containers)"
        echo "{\"status\":\"partial\",\"container_count\":${container_count},\"failed_services\":${failed_services}}"
        return 1
    else
        log_error "IOP is not installed"
        echo "{\"status\":\"not_installed\",\"container_count\":0,\"failed_services\":${failed_services}}"
        return 1
    fi
}

# Verify CVE sync
verify_cve_sync() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Verifying CVE synchronization"

    local cve_count
    cve_count=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                       "${satellite_user}" "${satellite_host}" \
                                       "sudo -n podman exec iop-service-vmaas-reposcan psql -U vmaas_writer -d vmaas -tAc 'SELECT COUNT(*) FROM cve;' 2>/dev/null || echo '0'" "false")

    local vmaas_repo_count
    vmaas_repo_count=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                              "${satellite_user}" "${satellite_host}" \
                                              "sudo -n podman exec iop-service-vmaas-reposcan psql -U vmaas_writer -d vmaas -tAc 'SELECT COUNT(*) FROM repo;' 2>/dev/null || echo '0'" "false")

    local vuln_repo_count
    vuln_repo_count=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                             "${satellite_user}" "${satellite_host}" \
                                             "sudo -n podman exec iop-service-vuln-manager psql -U vuln_user -d vulnerability -tAc 'SELECT COUNT(*) FROM repository;' 2>/dev/null || echo '0'" "false")

    local os_metadata_count
    os_metadata_count=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                               "${satellite_user}" "${satellite_host}" \
                                               "sudo -n podman exec iop-service-vuln-manager psql -U vuln_user -d vulnerability -tAc 'SELECT COUNT(*) FROM metadata;' 2>/dev/null || echo '0'" "false")

    log_info "CVE count: ${cve_count}"
    log_info "VMaaS repositories: ${vmaas_repo_count}"
    log_info "Vulnerability Engine repositories: ${vuln_repo_count}"
    log_info "OS metadata: ${os_metadata_count}"

    local status="ok"
    if [[ "${cve_count}" -eq 0 ]]; then
        log_error "No CVE data synced"
        status="failed"
    elif [[ "${vmaas_repo_count}" -eq 0 ]]; then
        log_error "No VMaaS repositories synced"
        status="failed"
    elif [[ "${vuln_repo_count}" -eq 0 ]]; then
        log_warn "No Vulnerability Engine repositories synced"
        status="warning"
    fi

    echo "{\"status\":\"${status}\",\"cve_count\":${cve_count},\"vmaas_repos\":${vmaas_repo_count},\"vuln_repos\":${vuln_repo_count},\"os_metadata\":${os_metadata_count}}"

    [[ "${status}" != "failed" ]]
}

# Verify PostgreSQL errors
verify_postgresql_errors() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"

    log_step "Checking for PostgreSQL errors"

    local since_time="${EXECUTION_START_TIME:-1 hour ago}"
    local pg_errors
    pg_errors=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                       "${satellite_user}" "${satellite_host}" \
                                       "sudo -n podman logs iop-service-vmaas-reposcan --since '${since_time}' 2>&1 | grep -c 'stack depth limit\|StatementTooComplex' || echo '0'" "false")

    if [[ "${pg_errors}" -gt 0 ]]; then
        log_warn "PostgreSQL errors detected (${pg_errors} occurrences)"
        echo "{\"status\":\"errors_found\",\"count\":${pg_errors}}"
        return 1
    else
        log_success "No PostgreSQL errors detected"
        echo "{\"status\":\"no_errors\",\"count\":0}"
        return 0
    fi
}

# Verify node1 Insights Client
verify_node1_insights() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local node1_user="$4"
    local node1_host="$5"

    log_step "Verifying node1 Insights Client"

    local insights_version
    insights_version=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                              "${node1_user}" "${node1_host}" \
                                              "rpm -q insights-client --qf '%{VERSION}-%{RELEASE}' 2>/dev/null || echo 'not_installed'" "false")

    if [[ "${insights_version}" == "not_installed" ]]; then
        log_error "insights-client is not installed"
        echo "{\"status\":\"not_installed\",\"version\":\"${insights_version}\"}"
        return 1
    fi

    log_info "insights-client version: ${insights_version}"

    local insights_status
    insights_status=$(execute_remote_command "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                             "${node1_user}" "${node1_host}" \
                                             "insights-client --status 2>&1" "true" || echo "error")

    local is_registered="false"
    if ! echo "${insights_status}" | grep -qi "not registered"; then
        is_registered="true"
        log_success "insights-client is registered"
    else
        log_error "insights-client is not registered"
    fi

    echo "{\"status\":\"installed\",\"version\":\"${insights_version}\",\"registered\":${is_registered}}"

    [[ "${is_registered}" == "true" ]]
}

# Generate verification report
generate_verification_report() {
    local bastion_user="$1"
    local bastion_host="$2"
    local bastion_port="$3"
    local satellite_user="$4"
    local satellite_host="$5"
    local node1_user="${6:-}"
    local node1_host="${7:-}"
    local report_file="${8:-}"

    log_info "=== Starting Verification ==="

    local total_checks=0
    local passed_checks=0
    local failed_checks=0
    local warning_checks=0

    local satellite_info dns_config iop_status cve_sync pg_errors insights_status

    # Satellite checks
    satellite_info=$(verify_satellite_info "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                           "${satellite_user}" "${satellite_host}")
    total_checks=$((total_checks + 1))
    passed_checks=$((passed_checks + 1))

    if dns_config=$(verify_dns_config "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                      "${satellite_user}" "${satellite_host}"); then
        passed_checks=$((passed_checks + 1))
    else
        failed_checks=$((failed_checks + 1))
    fi
    total_checks=$((total_checks + 1))

    if iop_status=$(verify_iop_installation "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                            "${satellite_user}" "${satellite_host}"); then
        passed_checks=$((passed_checks + 1))
    else
        if echo "${iop_status}" | grep -q '"status":"partial"'; then
            warning_checks=$((warning_checks + 1))
        else
            failed_checks=$((failed_checks + 1))
        fi
    fi
    total_checks=$((total_checks + 1))

    if cve_sync=$(verify_cve_sync "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                  "${satellite_user}" "${satellite_host}"); then
        passed_checks=$((passed_checks + 1))
    else
        if echo "${cve_sync}" | grep -q '"status":"warning"'; then
            warning_checks=$((warning_checks + 1))
        else
            failed_checks=$((failed_checks + 1))
        fi
    fi
    total_checks=$((total_checks + 1))

    if pg_errors=$(verify_postgresql_errors "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                            "${satellite_user}" "${satellite_host}"); then
        passed_checks=$((passed_checks + 1))
    else
        warning_checks=$((warning_checks + 1))
    fi
    total_checks=$((total_checks + 1))

    # Node1 checks (if provided)
    if [[ -n "${node1_user}" && -n "${node1_host}" ]]; then
        if insights_status=$(verify_node1_insights "${bastion_user}" "${bastion_host}" "${bastion_port}" \
                                                    "${node1_user}" "${node1_host}"); then
            passed_checks=$((passed_checks + 1))
        else
            failed_checks=$((failed_checks + 1))
        fi
        total_checks=$((total_checks + 1))
    else
        insights_status="{\"status\":\"not_checked\"}"
    fi

    # Determine overall status
    local overall_status="success"
    if [[ "${failed_checks}" -gt 0 ]]; then
        overall_status="failed"
    elif [[ "${warning_checks}" -gt 0 ]]; then
        overall_status="warning"
    fi

    # Print summary
    log_info "=== Verification Summary ==="
    log_info "Total checks: ${total_checks}"
    log_info "Passed: ${passed_checks}"
    log_info "Warnings: ${warning_checks}"
    log_info "Failed: ${failed_checks}"
    log_info "Overall status: ${overall_status}"

    # Generate JSON report if requested
    if [[ -n "${report_file}" ]] && ! is_dry_run; then
        local timestamp
        timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

        cat > "${report_file}" <<EOF
{
  "timestamp": "${timestamp}",
  "satellite": ${satellite_info},
  "dns_config": ${dns_config},
  "lightspeed": ${iop_status},
  "vulnerability_sync": ${cve_sync},
  "postgresql_errors": ${pg_errors},
  "insights_client": ${insights_status},
  "summary": {
    "status": "${overall_status}",
    "total_checks": ${total_checks},
    "passed_checks": ${passed_checks},
    "failed_checks": ${failed_checks},
    "warning_checks": ${warning_checks}
  }
}
EOF

        log_success "Verification report saved to ${report_file}"
    fi

    if [[ "${overall_status}" == "success" ]]; then
        log_success "All verification checks passed"
        return 0
    elif [[ "${overall_status}" == "warning" ]]; then
        log_warn "Verification completed with warnings"
        return 0
    else
        log_error "Verification failed"
        return 1
    fi
}

# If script is executed directly (not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [[ $# -lt 5 ]]; then
        echo "Usage: $0 <bastion_user> <bastion_host> <bastion_port> <satellite_user> <satellite_host> [node1_user] [node1_host] [report_file]" >&2
        exit 1
    fi

    generate_verification_report "$@"
fi
