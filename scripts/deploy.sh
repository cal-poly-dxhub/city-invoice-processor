#!/bin/bash
set -e

cd "$(dirname "$0")/.."

echo "Creating virtual environment..."
python3 -m venv .venv

echo "Activating virtual environment..."
source .venv/bin/activate

echo "Installing CDK dependencies..."
pip install -r cdk/requirements.txt

echo "Getting AWS account ID and region..."
export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export CDK_DEFAULT_REGION=${AWS_DEFAULT_REGION:-$(aws configure get region)}

if [ -z "$CDK_DEFAULT_REGION" ]; then
    echo "Error: AWS region not configured. Set AWS_DEFAULT_REGION or configure AWS CLI."
    exit 1
fi

echo "Creating CDK bootstrap bucket..."
BUCKET_NAME="cdk-hnb659fds-assets-$CDK_DEFAULT_ACCOUNT-$CDK_DEFAULT_REGION"
if ! aws s3 ls s3://$BUCKET_NAME 2>/dev/null; then
    echo "Creating bucket: $BUCKET_NAME"
    aws s3 mb s3://$BUCKET_NAME --region $CDK_DEFAULT_REGION
else
    echo "Bucket already exists: $BUCKET_NAME"
fi

echo "Bootstrapping CDK for account $CDK_DEFAULT_ACCOUNT in region $CDK_DEFAULT_REGION..."
cd cdk
cdk bootstrap aws://$CDK_DEFAULT_ACCOUNT/$CDK_DEFAULT_REGION

echo "Deploying Lambda function..."
cdk deploy --require-approval never

echo "Deployment complete!"
