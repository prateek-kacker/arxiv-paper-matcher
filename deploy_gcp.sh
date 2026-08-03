#!/usr/bin/env bash
# ==============================================================================
# Automated GCP Deployment Script for arXiv CS.CL Paper Matcher
# Architecture: Streamlit Dashboard (Cloud Run Service)
#               + Batch Runner (Cloud Run Job for Recurring Schedules)
#               + GCP Cloud Scheduler + Google Cloud Storage (GCS)
# ==============================================================================

set -e

# Configuration (Customize as needed or set via env vars)
PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo 'your-gcp-project-id')}"
REGION="${GCP_REGION:-us-central1}"
REPO_NAME="${GCP_REPO_NAME:-paper-matcher-repo}"
IMAGE_NAME="archive-paper-matcher"
SERVICE_NAME="archive-paper-matcher"
JOB_NAME="archive-paper-matcher-job"
SCHEDULER_JOB_NAME="hourly-paper-matcher-eval"
BUCKET_NAME="${PAPER_MATCHER_DB_BUCKET:-${PROJECT_ID}-paper-matcher-data}"

echo "======================================================================"
echo "🚀 Starting GCP Deployment for arXiv CS.CL Paper Matcher"
echo "Project ID: ${PROJECT_ID}"
echo "Region:     ${REGION}"
echo "Bucket:     ${BUCKET_NAME}"
echo "======================================================================"

# 1. Enable Required GCP APIs
echo "📡 Enabling required GCP APIs..."
gcloud services enable \
    artifactregistry.googleapis.com \
    run.googleapis.com \
    cloudscheduler.googleapis.com \
    cloudbuild.googleapis.com \
    storage.googleapis.com \
    --project="${PROJECT_ID}"

# 2. Create Storage Bucket if it doesn't exist
echo "🗄️ Setting up Google Cloud Storage bucket for paper_matcher.db..."
if ! gsutil ls -b "gs://${BUCKET_NAME}" &>/dev/null; then
    gcloud storage buckets create "gs://${BUCKET_NAME}" --location="${REGION}" --project="${PROJECT_ID}"
    echo "Created GCS bucket: gs://${BUCKET_NAME}"
else
    echo "GCS bucket already exists: gs://${BUCKET_NAME}"
fi

# 3. Create Artifact Registry Repository if needed
echo "📦 Setting up Artifact Registry..."
if ! gcloud artifacts repositories describe "${REPO_NAME}" --location="${REGION}" &>/dev/null; then
    gcloud artifacts repositories create "${REPO_NAME}" \
        --repository-format=docker \
        --location="${REGION}" \
        --description="Docker repository for arXiv Paper Matcher" \
        --project="${PROJECT_ID}"
fi

IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:latest"

# 4. Build and Push Container Image using Cloud Build
echo "🔨 Building container image with Cloud Build..."
gcloud builds submit --tag "${IMAGE_URI}" . --project="${PROJECT_ID}"

# 5. Deploy Streamlit Web Dashboard (Cloud Run Service)
echo "🌐 Deploying Streamlit Dashboard to Cloud Run Service..."
gcloud run deploy "${SERVICE_NAME}" \
    --image="${IMAGE_URI}" \
    --region="${REGION}" \
    --platform=managed \
    --allow-unauthenticated \
    --set-env-vars="PAPER_MATCHER_DB_BUCKET=${BUCKET_NAME},GCP_PROJECT_ID=${PROJECT_ID},GCP_LOCATION=${REGION}" \
    --memory=2Gi \
    --cpu=2 \
    --project="${PROJECT_ID}"

SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --region="${REGION}" --format="value(status.url)" --project="${PROJECT_ID}")
echo "✅ Streamlit Dashboard URL: ${SERVICE_URL}"

# 6. Create or Update Cloud Run Job (Headless Batch Runner for Recurring Schedules)
echo "⚡ Deploying Cloud Run Job for recurring schedules..."
if gcloud run jobs describe "${JOB_NAME}" --region="${REGION}" --project="${PROJECT_ID}" &>/dev/null; then
    gcloud run jobs update "${JOB_NAME}" \
        --image="${IMAGE_URI}" \
        --region="${REGION}" \
        --command="python" \
        --args="batch_runner.py" \
        --set-env-vars="PAPER_MATCHER_DB_BUCKET=${BUCKET_NAME},GCP_PROJECT_ID=${PROJECT_ID}" \
        --set-secrets="GEMINI_API_KEY=GEMINI_API_KEY:latest" \
        --memory=2Gi \
        --cpu=2 \
        --task-timeout=30m \
        --project="${PROJECT_ID}"
else
    gcloud run jobs create "${JOB_NAME}" \
        --image="${IMAGE_URI}" \
        --region="${REGION}" \
        --command="python" \
        --args="batch_runner.py" \
        --set-env-vars="PAPER_MATCHER_DB_BUCKET=${BUCKET_NAME},GCP_PROJECT_ID=${PROJECT_ID}" \
        --set-secrets="GEMINI_API_KEY=GEMINI_API_KEY:latest" \
        --memory=2Gi \
        --cpu=2 \
        --task-timeout=30m \
        --project="${PROJECT_ID}"
fi

# 7. Create Cloud Scheduler Cron Job (Runs hourly to trigger due schedules)
JOB_RUN_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"
echo "⏰ Configuring GCP Cloud Scheduler job..."

if gcloud scheduler jobs describe "${SCHEDULER_JOB_NAME}" --location="${REGION}" --project="${PROJECT_ID}" &>/dev/null; then
    echo "Cloud Scheduler job '${SCHEDULER_JOB_NAME}' already exists."
else
    # Create Service Account for Cloud Scheduler execution
    SA_NAME="paper-matcher-scheduler-sa"
    SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

    if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" &>/dev/null; then
        gcloud iam service-accounts create "${SA_NAME}" --display-name="Paper Matcher Cloud Scheduler SA" --project="${PROJECT_ID}"
        gcloud run jobs add-iam-policy-binding "${JOB_NAME}" \
            --region="${REGION}" \
            --member="serviceAccount:${SA_EMAIL}" \
            --role="roles/run.invoker" \
            --project="${PROJECT_ID}"
    fi

    # Trigger Cloud Run Job every hour at :00 (0 * * * *)
    gcloud scheduler jobs create http "${SCHEDULER_JOB_NAME}" \
        --schedule="0 * * * *" \
        --location="${REGION}" \
        --uri="${JOB_RUN_URI}" \
        --http-method=POST \
        --oauth-service-account-email="${SA_EMAIL}" \
        --project="${PROJECT_ID}"
    echo "✅ Created Cloud Scheduler job triggering hourly (0 * * * *)."
fi

echo "======================================================================"
echo "🎉 GCP Deployment Completed Successfully!"
echo "Dashboard URL:   ${SERVICE_URL}"
echo "Cloud Run Job:   ${JOB_NAME}"
echo "GCS DB Bucket:   gs://${BUCKET_NAME}"
echo "======================================================================"
