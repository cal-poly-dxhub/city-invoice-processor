"""Storage stack: S3 buckets and DynamoDB table."""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct


class StorageStack(Stack):
    """S3 buckets for data/frontend and DynamoDB cache table."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- S3 Data Bucket ---
        # Holds uploads/{job_id}/... and jobs/{job_id}/...
        self.data_bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=None,  # auto-generated
            removal_policy=RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            event_bridge_enabled=True,  # needed for EventBridge S3 trigger
            cors=[
                s3.CorsRule(
                    allowed_methods=[
                        s3.HttpMethods.GET,
                        s3.HttpMethods.PUT,
                    ],
                    allowed_origins=["*"],  # tightened after CloudFront deploy
                    allowed_headers=["*"],
                    max_age=3600,
                )
            ],
        )

        # Allow CloudFront OAC to read from the data bucket.
        # Added here (not in FrontendStack) to avoid cross-stack cyclic dependency.
        self.data_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudFrontOAC",
                principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
                actions=["s3:GetObject"],
                resources=[self.data_bucket.arn_for_objects("*")],
            )
        )

        # --- DynamoDB Cache Table ---
        # Replaces SQLite index_store for serverless caching
        self.cache_table = dynamodb.Table(
            self,
            "CacheTable",
            table_name=None,  # auto-generated
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            time_to_live_attribute="ttl",
        )
