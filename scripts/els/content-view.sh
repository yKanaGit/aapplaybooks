#!/bin/bash
# Content View management functions

create_els_content_view() {
    log_info "=== Creating ELS Content View ==="

    if is_dry_run; then
        log_info "[DRY-RUN] Would create Content View: ${ELS_CONTENT_VIEW}"
        log_info "[DRY-RUN] Would add base repositories from: ${BASE_CONTENT_VIEW}"
        log_info "[DRY-RUN] Would add ELS repository: rhel-7-server-els-rpms"
        log_info "[DRY-RUN] Would publish and promote to: ${LIFECYCLE_ENV}"
        log_info "[DRY-RUN] NOTE: node3 will NOT be assigned (kept for enable-els step)"
        return 0
    fi

    # Get organization ID
    local org_output
    org_output=$(satellite_exec "hammer --output json organization list --search 'name=\"${ORGANIZATION}\"' 2>/dev/null" "Get organization")
    local org_id
    org_id=$(echo "${org_output}" | jq -r '.[0].Id // empty')

    if [[ -z "${org_id}" ]]; then
        log_error "Organization not found"
        return 1
    fi

    # Get base Content View repositories
    log_info "Getting repositories from ${BASE_CONTENT_VIEW}..."
    local base_cv_output
    base_cv_output=$(satellite_exec "hammer --output json content-view info --organization-id ${org_id} --name '${BASE_CONTENT_VIEW}' 2>/dev/null" "Get base Content View")
    local base_repo_ids
    base_repo_ids=$(echo "${base_cv_output}" | jq -r '.Repositories[].ID' | tr '\n' ' ')

    if [[ -z "${base_repo_ids}" ]]; then
        log_error "No repositories found in ${BASE_CONTENT_VIEW}"
        return 1
    fi

    log_success "Found repositories: ${base_repo_ids}"

    # Get ELS repository ID
    local els_repo_output
    els_repo_output=$(satellite_exec "hammer --output json repository list --organization-id ${org_id} --search 'label = \"rhel-7-server-els-rpms\"' 2>/dev/null" "Get ELS repository")
    local els_repo_id
    els_repo_id=$(echo "${els_repo_output}" | jq -r '.[0].ID // empty')

    if [[ -z "${els_repo_id}" ]]; then
        log_error "ELS repository not found"
        return 1
    fi

    log_success "ELS Repository ID: ${els_repo_id}"

    # Combine repository IDs
    local all_repo_ids="${base_repo_ids}${els_repo_id}"

    # Check if ELS Content View already exists
    local els_cv_output
    els_cv_output=$(satellite_exec "hammer --output json content-view list --organization-id ${org_id} --search 'name=\"${ELS_CONTENT_VIEW}\"' 2>/dev/null" "Check ELS Content View")
    local els_cv_id
    els_cv_id=$(echo "${els_cv_output}" | jq -r '.[0].\"Content View ID\" // empty')

    if [[ -n "${els_cv_id}" ]]; then
        log_success "ELS Content View already exists (ID: ${els_cv_id})"
    else
        log_info "Creating ELS Content View..."

        if is_dry_run; then
            log_info "[DRY-RUN] Would create Content View: ${ELS_CONTENT_VIEW}"
        else
            satellite_exec "hammer content-view create --organization-id ${org_id} --name '${ELS_CONTENT_VIEW}' --description 'RHEL 7 with ELS repositories'" \
                "Create ELS Content View"

            els_cv_output=$(satellite_exec "hammer --output json content-view list --organization-id ${org_id} --search 'name=\"${ELS_CONTENT_VIEW}\"' 2>/dev/null" "Get new CV ID")
            els_cv_id=$(echo "${els_cv_output}" | jq -r '.[0].\"Content View ID\" // empty')
        fi
    fi

    # Add repositories to Content View
    log_info "Adding repositories to ELS Content View..."

    if is_dry_run; then
        log_info "[DRY-RUN] Would add repositories: ${all_repo_ids}"
    else
        for repo_id in ${all_repo_ids}; do
            satellite_exec "hammer content-view add-repository --organization-id ${org_id} --id ${els_cv_id} --repository-id ${repo_id}" \
                "Add repository ${repo_id}"
        done
    fi

    # Publish Content View
    log_info "Publishing ELS Content View..."

    if is_dry_run; then
        log_info "[DRY-RUN] Would publish Content View"
    else
        satellite_exec "hammer content-view publish --organization-id ${org_id} --id ${els_cv_id} --async" \
            "Publish ELS Content View"

        # Wait for publish to complete
        log_info "Waiting for publish to complete..."
        sleep 60  # Initial wait
    fi

    # Promote to Lifecycle Environment
    log_info "Promoting to ${LIFECYCLE_ENV}..."

    # Get lifecycle environment ID
    local lce_output
    lce_output=$(satellite_exec "hammer --output json lifecycle-environment list --organization-id ${org_id} --search 'name=\"${LIFECYCLE_ENV}\"' 2>/dev/null" "Get lifecycle environment")
    local lce_id
    lce_id=$(echo "${lce_output}" | jq -r '.[0].ID // empty')

    if [[ -z "${lce_id}" ]]; then
        log_error "Lifecycle Environment not found: ${LIFECYCLE_ENV}"
        return 1
    fi

    # Get latest version
    local cv_info
    cv_info=$(satellite_exec "hammer --output json content-view info --organization-id ${org_id} --id ${els_cv_id} 2>/dev/null" "Get CV info")
    local latest_version
    latest_version=$(echo "${cv_info}" | jq -r '.Versions[0].ID // empty')

    if [[ -z "${latest_version}" ]]; then
        log_error "No version found for ELS Content View"
        return 1
    fi

    if is_dry_run; then
        log_info "[DRY-RUN] Would promote version ${latest_version} to ${LIFECYCLE_ENV}"
    else
        satellite_exec "hammer content-view version promote --organization-id ${org_id} --id ${latest_version} --to-lifecycle-environment-id ${lce_id} --async" \
            "Promote to ${LIFECYCLE_ENV}"
    fi

    log_success "ELS Content View created and published"
    return 0
}

# Assign node3 to ELS Content View
assign_node3_to_els_content_view() {
    log_info "=== Assigning node3 to ELS Content View ==="

    if is_dry_run; then
        log_info "[DRY-RUN] Would assign ${NODE3_HOST} to ${LIFECYCLE_ENV}/${ELS_CONTENT_VIEW}"
        return 0
    fi

    # Get organization ID
    local org_output
    org_output=$(satellite_exec "hammer --output json organization list --search 'name=\"${ORGANIZATION}\"' 2>/dev/null" "Get organization")
    local org_id
    org_id=$(echo "${org_output}" | jq -r '.[0].Id // empty')

    if [[ -z "${org_id}" ]]; then
        log_error "Organization not found"
        return 1
    fi

    log_info "Assigning ${NODE3_HOST} to ${ELS_CONTENT_VIEW}..."

    satellite_exec "hammer host update --organization-id ${org_id} --name '${NODE3_HOST}' --content-view '${ELS_CONTENT_VIEW}' --lifecycle-environment '${LIFECYCLE_ENV}'" \
        "Assign node3 to ELS Content View"

    # Wait for subscription manager to sync
    log_info "Waiting for Content View assignment to propagate..."
    sleep 10

    log_success "node3 assigned to ELS Content View"
    return 0
}
