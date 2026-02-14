#!/bin/bash

# Invoice Reconciliation CDK Deployment Script

set -e

echo "Starting Invoice Reconciliation deployment..."

# Check if environment is set
ENVIRONMENT=${ENVIRONMENT:-dev}
echo "Deploying to environment: $ENVIRONMENT"

# Navigate to infra directory for CDK operations
cd infra

# Create and activate virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi
echo "Activating virtual environment..."
source venv/bin/activate

# Install CDK dependencies
echo "Installing CDK dependencies..."
pip install -r requirements.txt

# Ensure CDK CLI is available
if ! command -v cdk &> /dev/null; then
    echo "Installing AWS CDK CLI..."
    npm install -g aws-cdk
fi

# Go back to project root for frontend build
cd ..

# Build frontend (first pass — without API URL)
echo "Building frontend..."
./scripts/build-frontend.sh

# Go back to infra for CDK commands
cd infra
source venv/bin/activate

# Bootstrap CDK (only needed once per account/region)
echo "Bootstrapping CDK..."
cdk bootstrap

# Deploy all stacks
echo "Deploying all stacks..."
cdk deploy --all --require-approval never --outputs-file outputs.json

# Extract API URL and CloudFront URL from outputs
cd ..
API_URL=$(python3 -c "
import json
data = json.load(open('infra/outputs.json'))
for stack_name, outputs in data.items():
    if 'ApiUrl' in outputs:
        print(outputs['ApiUrl'])
        break
")
CLOUDFRONT_URL=$(python3 -c "
import json
data = json.load(open('infra/outputs.json'))
for stack_name, outputs in data.items():
    if 'CloudFrontURL' in outputs:
        print(outputs['CloudFrontURL'])
        break
")

echo "API Gateway URL: $API_URL"
echo "CloudFront URL: $CLOUDFRONT_URL"

# Update frontend configuration with API URL
echo "Updating frontend API configuration..."
./scripts/update-frontend-config.sh "$API_URL"

# Rebuild frontend with new API URL
echo "Rebuilding frontend with API URL..."
./scripts/build-frontend.sh

# Redeploy frontend stack with updated build
cd infra
source venv/bin/activate
echo "Redeploying frontend with updated build..."
cdk deploy InvoiceProcessorFrontend --require-approval never

echo ""
echo "Deployment completed successfully!"
echo ""
echo "Application URLs:"
echo "  Frontend: $CLOUDFRONT_URL"
echo "  API: $API_URL"
