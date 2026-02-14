"""Frontend stack: S3 static hosting + CloudFront distribution."""

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
)
from constructs import Construct

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class FrontendStack(Stack):
    """S3 static hosting and CloudFront with 3 origins."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data_bucket: s3.IBucket,
        api_url: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Frontend S3 Bucket ---
        frontend_bucket = s3.Bucket(
            self,
            "FrontendBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # Import the data bucket by name within this stack to avoid a
        # cross-stack cyclic reference.  The StorageStack already has a
        # bucket policy granting CloudFront OAC read access.
        imported_data_bucket = s3.Bucket.from_bucket_name(
            self, "ImportedDataBucket", data_bucket.bucket_name
        )

        # --- CloudFront Distribution (3 origins using OAC) ---
        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(
                    frontend_bucket
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            additional_behaviors={
                # Serve reconciliation.json and other job outputs
                # S3 key: jobs/{uuid}/reconciliation.json
                "/jobs/*": cloudfront.BehaviorOptions(
                    origin=origins.S3BucketOrigin.with_origin_access_control(
                        imported_data_bucket,
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                ),
                # Serve uploaded PDFs for review UI
                # S3 key: uploads/{uuid}/pdf/{filename}
                "/uploads/*": cloudfront.BehaviorOptions(
                    origin=origins.S3BucketOrigin.with_origin_access_control(
                        imported_data_bucket,
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                ),
            },
            # SPA fallback: 403/404 -> /index.html for client-side routing
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
            ],
        )

        # --- Deploy frontend build to S3 ---
        frontend_dist = PROJECT_ROOT / "frontend" / "dist"
        if frontend_dist.exists():
            s3deploy.BucketDeployment(
                self,
                "DeployFrontend",
                sources=[s3deploy.Source.asset(str(frontend_dist))],
                destination_bucket=frontend_bucket,
                distribution=distribution,
                distribution_paths=["/*"],
            )

        # --- Outputs ---
        CfnOutput(
            self,
            "CloudFrontURL",
            value=f"https://{distribution.distribution_domain_name}",
            description="CloudFront distribution URL",
        )

        CfnOutput(
            self,
            "FrontendBucketName",
            value=frontend_bucket.bucket_name,
            description="Frontend S3 bucket name",
        )

        CfnOutput(
            self,
            "ApiUrl",
            value=api_url,
            description="API Gateway URL",
        )
