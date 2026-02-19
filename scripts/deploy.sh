#!/bin/bash

# Invoice Reconciliation CDK Deployment Script
#
# The CDK FrontendStack automatically runs `npm ci && npm run build` in
# frontend/ during synthesis, so no separate frontend build step is needed.
# The CloudFront distribution proxies /api/* to API Gateway, so no
# VITE_API_URL configuration is required.

set -e

echo "Starting Invoice Reconciliation deployment..."

ENVIRONMENT=${ENVIRONMENT:-dev}
echo "Deploying to environment: $ENVIRONMENT"

# Navigate to infra directory for CDK operations
cd "$(dirname "$0")/../infra"

# Create and activate virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi
echo "Activating virtual environment..."
source venv/bin/activate

# Install CDK dependencies
echo "Installing CDK dependencies..."
pip install -r requirements.txt -q

# Ensure CDK CLI is available
if ! command -v cdk &> /dev/null; then
    echo "Installing AWS CDK CLI..."
    npm install -g aws-cdk
fi

# Bootstrap CDK (only needed once per account/region)
echo "Bootstrapping CDK..."
cdk bootstrap

# Deploy all stacks (frontend is built automatically during synth)
echo "Deploying all stacks..."
cdk deploy --all --require-approval never --outputs-file outputs.json

# Extract URLs from outputs
cd ..
CLOUDFRONT_URL=$(python3 -c "
import json
data = json.load(open('infra/outputs.json'))
for stack_name, outputs in data.items():
    if 'CloudFrontURL' in outputs:
        print(outputs['CloudFrontURL'])
        break
")
API_URL=$(python3 -c "
import json
data = json.load(open('infra/outputs.json'))
for stack_name, outputs in data.items():
    if 'ApiUrl' in outputs:
        print(outputs['ApiUrl'])
        break
")

echo ""
echo "Deployment completed successfully!"
echo ""
echo "Application URLs:"
echo "  Frontend: $CLOUDFRONT_URL"
echo "  API (direct): $API_URL"
echo "  API (via CloudFront): $CLOUDFRONT_URL/api/"
