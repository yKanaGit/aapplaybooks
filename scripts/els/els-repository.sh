#!/bin/bash
# ELS repository management functions

setup_els_repository() {
    log_info "=== Setting up ELS Repository ==="

    if is_dry_run; then
        log_info "[DRY-RUN] Would setup ELS repository for organization: ${ORGANIZATION}"
        log_info "[DRY-RUN] Would enable and synchronize rhel-7-server-els-rpms"
        return 0
    fi

    # Get organization ID
    log_info "Getting organization ID..."
    local org_output
    org_output=$(satellite_exec "hammer --output json organization list --search 'name=\"${ORGANIZATION}\"' 2>/dev/null" "Get organization")
    local org_id
    org_id=$(echo "${org_output}" | jq -r '.[0].Id // empty')

    if [[ -z "${org_id}" ]]; then
        log_error "Organization not found: ${ORGANIZATION}"
        return 1
    fi

    log_success "Organization ID: ${org_id}"

    # Find ELS repository set
    log_info "Finding ELS repository set..."
    local repo_set_output
    repo_set_output=$(satellite_exec "hammer --output json repository-set list --organization-id ${org_id} --search 'name ~ \"Extended Life Cycle Support\"' 2>/dev/null" "Find ELS repository set")
    local repo_set_id
    repo_set_id=$(echo "${repo_set_output}" | jq -r '.[0].ID // empty')

    if [[ -z "${repo_set_id}" ]]; then
        log_error "ELS repository set not found"
        return 1
    fi

    log_success "ELS Repository Set ID: ${repo_set_id}"

    # Enable ELS repository
    log_info "Enabling ELS repository..."

    if is_dry_run; then
        log_info "[DRY-RUN] Would enable ELS repository (Set ID: ${repo_set_id})"
    else
        satellite_exec "hammer repository-set enable --organization-id ${org_id} --id ${repo_set_id} --basearch x86_64 --releasever 7Server" \
            "Enable ELS repository"

        if [[ $? -ne 0 ]]; then
            log_warning "ELS repository may already be enabled"
        fi
    fi

    # Find enabled ELS repository
    log_info "Finding enabled ELS repository..."
    local repo_output
    repo_output=$(satellite_exec "hammer --output json repository list --organization-id ${org_id} --search 'label = \"rhel-7-server-els-rpms\"' 2>/dev/null" "Find ELS repository")
    local repo_id
    repo_id=$(echo "${repo_output}" | jq -r '.[0].ID // empty')

    if [[ -z "${repo_id}" ]]; then
        log_error "Enabled ELS repository not found"
        return 1
    fi

    log_success "ELS Repository ID: ${repo_id}"

    # Synchronize repository
    log_info "Synchronizing ELS repository (this may take several minutes)..."

    if is_dry_run; then
        log_info "[DRY-RUN] Would synchronize ELS repository (ID: ${repo_id})"
    else
        satellite_exec "hammer repository synchronize --organization-id ${org_id} --id ${repo_id} --async" \
            "Start ELS repository sync"

        # Wait for sync to complete
        log_info "Waiting for repository sync to complete..."
        local max_wait=1800  # 30 minutes
        local wait_interval=30
        local elapsed=0

        while [[ ${elapsed} -lt ${max_wait} ]]; do
            local sync_status
            sync_status=$(satellite_exec "hammer --output json repository info --organization-id ${org_id} --id ${repo_id} 2>/dev/null" "Check sync status")
            local last_sync
            last_sync=$(echo "${sync_status}" | jq -r '.\"Last Sync\".Result // empty')

            if [[ "${last_sync}" == "success" ]]; then
                log_success "ELS repository synchronized successfully"
                return 0
            elif [[ "${last_sync}" == "error" ]]; then
                log_error "ELS repository sync failed"
                return 1
            fi

            sleep ${wait_interval}
            elapsed=$((elapsed + wait_interval))
            log_info "Sync still in progress... (${elapsed}/${max_wait}s)"
        done

        log_error "Timeout waiting for repository sync"
        return 1
    fi

    return 0
}
