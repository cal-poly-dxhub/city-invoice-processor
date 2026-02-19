"""Frontend stack: S3 static hosting + CloudFront distribution.

The CloudFront distribution serves three origins:
  1. Frontend SPA (default) — S3 bucket with built React assets
  2. Data files (/jobs/*, /uploads/*) — S3 data bucket with reconciliation
     results and uploaded PDFs
  3. API proxy (/api/*) — API Gateway, so the frontend can call the API on
     the same origin (no CORS, no VITE_API_URL env var needed)
"""

import logging
import os
import subprocess
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Fn,
    RemovalPolicy,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
)
from constructs import Construct

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class FrontendStack(Stack):
    """S3 static hosting and CloudFront with API proxy."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data_bucket: s3.IBucket,
        api_url: str,
        api_rest_api_id: str,
        api_stage_name: str,
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

        # --- SPA routing via CloudFront Function ---
        # Rewrites non-file paths (no extension) to /index.html so that
        # client-side routes like /review/{jobId} work.  Unlike the old
        # distribution-level error_responses approach, this only affects the
        # default behavior (frontend bucket) and does NOT intercept real
        # 403/404 errors from the /jobs/* and /uploads/* data-bucket behaviors.
        spa_rewrite_fn = cloudfront.Function(
            self,
            "SpaRewriteFunction",
            code=cloudfront.FunctionCode.from_inline(
                "function handler(event) {\n"
                "  var request = event.request;\n"
                "  var uri = request.uri;\n"
                "  if (uri.indexOf('.') !== -1) {\n"
                "    return request;\n"
                "  }\n"
                "  request.uri = '/index.html';\n"
                "  return request;\n"
                "}\n"
            ),
        )

        # --- API Gateway origin for /api/* proxy ---
        # Construct the API Gateway domain:
        #   {rest_api_id}.execute-api.{region}.amazonaws.com
        # The stage name (e.g. "prod") becomes the origin_path so that
        # CloudFront strips it from the viewer-facing URL.
        api_domain = Fn.join("", [
            api_rest_api_id,
            ".execute-api.",
            self.region,
            ".amazonaws.com",
        ])
        api_origin = origins.HttpOrigin(
            api_domain,
            origin_path=Fn.join("", ["/", api_stage_name]),
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
        )

        # Use built-in policies for the API behavior:
        # - CACHING_DISABLED: no caching, forwards query strings automatically
        # - ALL_VIEWER_EXCEPT_HOST_HEADER: forwards all viewer headers
        #   (Content-Type, Accept, etc.) except Host to the API Gateway origin

        # --- CloudFront Distribution ---
        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(
                    frontend_bucket
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=spa_rewrite_fn,
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    ),
                ],
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
                # Proxy API requests to API Gateway (same origin, no CORS)
                "/api/*": cloudfront.BehaviorOptions(
                    origin=api_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                ),
            },
        )

        # --- Build and deploy frontend to S3 ---
        frontend_root = PROJECT_ROOT / "frontend"
        frontend_dist = frontend_root / "dist"

        if (frontend_root / "package.json").exists():
            logger.info("Building frontend: npm ci && npm run build")
            subprocess.run(
                ["npm", "ci"],
                cwd=str(frontend_root),
                check=True,
            )
            subprocess.run(
                ["npm", "run", "build"],
                cwd=str(frontend_root),
                check=True,
                env={**os.environ, "VITE_ENVIRONMENT": "production"},
            )

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
            description="API Gateway URL (direct — prefer CloudFront /api/* proxy)",
        )
