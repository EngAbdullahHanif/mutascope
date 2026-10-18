#!/usr/bin/env bash
# One-time Google Cloud setup for deploying the MutaScope API to Cloud Run (ADR 0034).
#
# Run it in Google Cloud Shell (gcloud is installed and logged in there), on a project that
# already has a billing account linked:
#
#   bash setup-gcp.sh <project-id>
#
# It is safe to run again: each step skips what already exists. It creates:
#   - the APIs: Cloud Run, Artifact Registry, IAM, IAM Credentials, Security Token Service;
#   - a Docker repository in Artifact Registry (keeps the 3 newest images, deletes the rest);
#   - two service accounts: the API's runtime identity (no roles at all) and the deployer
#     that GitHub Actions acts as (Cloud Run admin, write to that one repository, may run
#     services as the runtime account);
#   - Workload Identity Federation for GitHub: only this repository's release workflow, on a
#     version tag or on main, can act as the deployer. No key file is created anywhere;
#   - read access for the runtime account to each secret in SECRETS that exists (created by
#     hand in Secret Manager; their values never pass through this script).
# At the end it prints the two values to put into conf/deploy/cloudrun.yaml.
set -euo pipefail

PROJECT_ID="${1:?usage: bash setup-gcp.sh <project-id>}"
# Keep these equal to conf/deploy/cloudrun.yaml.
REGION=europe-west1
REPOSITORY=mutascope
RUNTIME=mutascope-api-runtime
DEPLOYER=mutascope-deployer
POOL=github
PROVIDER=github
GITHUB_REPO=EngAbdullahHanif/mutascope
# The secret names under `secrets` in conf/deploy/cloudrun.yaml.
SECRETS=(MUTASCOPE_API_METRICS_TOKEN MUTASCOPE_API_SENTRY_DSN)

RUNTIME_EMAIL="${RUNTIME}@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOYER_EMAIL="${DEPLOYER}@${PROJECT_ID}.iam.gserviceaccount.com"

step() { printf '\n==> %s\n' "$*"; }

step "Project ${PROJECT_ID}"
gcloud config set project "${PROJECT_ID}" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
BILLING="$(gcloud billing projects describe "${PROJECT_ID}" --format='value(billingEnabled)' 2>/dev/null || echo unknown)"
if [ "${BILLING}" = "False" ]; then
  echo "Billing is not enabled on ${PROJECT_ID}. Link a billing account first (Billing > My projects)." >&2
  exit 1
elif [ "${BILLING}" != "True" ]; then
  echo "Could not read the billing status; continuing (enabling the APIs fails without billing)."
fi

step "Enabling APIs (takes a minute the first time)"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com iam.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com secretmanager.googleapis.com

step "Artifact Registry repository ${REPOSITORY} (${REGION})"
if ! gcloud artifacts repositories describe "${REPOSITORY}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${REPOSITORY}" --repository-format=docker \
    --location="${REGION}" --description="MutaScope API images (pushed by GitHub Actions)"
fi
POLICY="$(mktemp)"
cat >"${POLICY}" <<'EOF'
[
  {"name": "keep-3-newest", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 3}},
  {"name": "delete-older", "action": {"type": "Delete"}, "condition": {"tagState": "any", "olderThan": "1d"}}
]
EOF
gcloud artifacts repositories set-cleanup-policies "${REPOSITORY}" --location="${REGION}" \
  --policy="${POLICY}" --no-dry-run >/dev/null
rm -f "${POLICY}"

step "Service accounts"
for pair in "${RUNTIME}|MutaScope API runtime (no roles)" "${DEPLOYER}|MutaScope deployer (GitHub Actions)"; do
  name="${pair%%|*}"
  if ! gcloud iam service-accounts describe "${name}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${name}" --display-name="${pair#*|}"
  fi
done

step "Deployer permissions"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member="serviceAccount:${DEPLOYER_EMAIL}" \
  --role=roles/run.admin --condition=None >/dev/null
gcloud artifacts repositories add-iam-policy-binding "${REPOSITORY}" --location="${REGION}" \
  --member="serviceAccount:${DEPLOYER_EMAIL}" --role=roles/artifactregistry.writer >/dev/null
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_EMAIL}" \
  --member="serviceAccount:${DEPLOYER_EMAIL}" --role=roles/iam.serviceAccountUser >/dev/null

step "Runtime access to the API's secrets"
# Per secret, not project-wide: the runtime account can read these and nothing else.
for secret in "${SECRETS[@]}"; do
  if gcloud secrets describe "${secret}" >/dev/null 2>&1; then
    gcloud secrets add-iam-policy-binding "${secret}" --member="serviceAccount:${RUNTIME_EMAIL}" \
      --role=roles/secretmanager.secretAccessor --condition=None >/dev/null
    echo "${RUNTIME} can read ${secret}"
  else
    echo "No secret ${secret} yet: create it, then run this script again."
  fi
done

step "Workload Identity Federation for ${GITHUB_REPO}"
if ! gcloud iam workload-identity-pools describe "${POOL}" --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${POOL}" --location=global --display-name="GitHub Actions"
fi
CONDITION="assertion.repository == '${GITHUB_REPO}' \
&& assertion.workflow_ref.startsWith('${GITHUB_REPO}/.github/workflows/release.yml@') \
&& (assertion.ref.startsWith('refs/tags/v') || assertion.ref == 'refs/heads/main')"
if ! gcloud iam workload-identity-pools providers describe "${PROVIDER}" --location=global \
  --workload-identity-pool="${POOL}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER}" --location=global \
    --workload-identity-pool="${POOL}" --display-name="GitHub" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="${CONDITION}"
fi
gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_EMAIL}" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}" \
  >/dev/null

step "Done. Put these two lines into conf/deploy/cloudrun.yaml:"
printf 'project_id: "%s"\nproject_number: "%s"\n' "${PROJECT_ID}" "${PROJECT_NUMBER}"
