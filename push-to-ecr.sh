#!/bin/bash

# Push Lambda Docker image to Amazon ECR
# Usage: ./push-to-ecr.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load environment variables from .env file
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "Loading configuration from .env..."
    export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)
else
    echo "Error: .env file not found!"
    echo "Copy .env.example to .env and configure your AWS settings:"
    echo "  cp .env.example .env"
    exit 1
fi

# Validate required variables
if [ -z "$AWS_ACCOUNT_ID" ]; then
    echo "Error: AWS_ACCOUNT_ID is not set in .env"
    exit 1
fi

if [ -z "$AWS_REGION" ]; then
    echo "Error: AWS_REGION is not set in .env"
    exit 1
fi

if [ -z "$ECR_REPOSITORY_NAME" ]; then
    echo "Error: ECR_REPOSITORY_NAME is not set in .env"
    exit 1
fi

# Set AWS authentication
if [ -n "$AWS_PROFILE" ]; then
    export AWS_PROFILE="$AWS_PROFILE"
    echo "Using AWS profile: $AWS_PROFILE"
elif [ -n "$AWS_ACCESS_KEY_ID" ] && [ -n "$AWS_SECRET_ACCESS_KEY" ]; then
    export AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID"
    export AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY"
    echo "Using AWS access keys from .env"
else
    echo "Using default AWS credentials (CLI config or IAM role)"
fi

# ECR registry URL
ECR_REGISTRY="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
IMAGE_URI="$ECR_REGISTRY/$ECR_REPOSITORY_NAME"

echo "============================================"
echo "Pushing Lambda to ECR"
echo "============================================"
echo "AWS Account:  $AWS_ACCOUNT_ID"
echo "Region:       $AWS_REGION"
echo "Repository:   $ECR_REPOSITORY_NAME"
echo "Image URI:    $IMAGE_URI"
echo "============================================"

# Create ECR repository if it doesn't exist
echo ""
echo "Checking ECR repository..."
if ! aws ecr describe-repositories --repository-names "$ECR_REPOSITORY_NAME" --region "$AWS_REGION" > /dev/null 2>&1; then
    echo "Creating ECR repository: $ECR_REPOSITORY_NAME"
    aws ecr create-repository \
        --repository-name "$ECR_REPOSITORY_NAME" \
        --region "$AWS_REGION" \
        --image-scanning-configuration scanOnPush=true
else
    echo "Repository already exists"
fi

# Authenticate Docker with ECR
echo ""
echo "Authenticating with ECR..."
aws ecr get-login-password --region "$AWS_REGION" | \
    docker login --username AWS --password-stdin "$ECR_REGISTRY"

# Build the Docker image
echo ""
echo "Building Docker image..."
cd "$SCRIPT_DIR"
docker build --platform linux/amd64 -t "$ECR_REPOSITORY_NAME" .

# Tag the image
echo ""
echo "Tagging image..."
docker tag "$ECR_REPOSITORY_NAME:latest" "$IMAGE_URI:latest"

# Also tag with timestamp for versioning
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
docker tag "$ECR_REPOSITORY_NAME:latest" "$IMAGE_URI:$TIMESTAMP"

# Push to ECR
echo ""
echo "Pushing image to ECR..."
docker push "$IMAGE_URI:latest"
docker push "$IMAGE_URI:$TIMESTAMP"

echo ""
echo "============================================"
echo "Push completed successfully!"
echo "============================================"
echo "Image URI: $IMAGE_URI:latest"
echo "Versioned: $IMAGE_URI:$TIMESTAMP"
echo ""
echo "Next steps:"
echo "1. Create or update your Lambda function with this image"
echo "2. Set the required environment variables in Lambda:"
echo "   - ANTHROPIC_API_KEY (required)"
echo "   - GOOGLE_CREDENTIALS_JSON (required, base64-encoded service account)"
echo "   - GOOGLE_DRIVE_FILE_IDS (required, comma-separated file IDs)"
echo "   - DATABASE_URL (optional, for persistence)"
echo "   - GENERATE_PDF (optional, set to 'true' to include PDF)"
echo "   - SAVE_TO_DB (optional, set to 'true' to persist results)"
echo "3. Set memory to 1024MB and timeout to 2 minutes"
echo "============================================"
