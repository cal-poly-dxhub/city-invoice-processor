"""Processing stack: Lambdas, Step Functions, EventBridge, API Gateway."""

from pathlib import Path

from aws_cdk import Duration, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

# Path to project root (infra/ is one level deep)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class ProcessingStack(Stack):
    """Lambdas, Step Functions state machine, EventBridge rule, API Gateway."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data_bucket: s3.IBucket,
        cache_table: dynamodb.ITable,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Common Lambda environment variables
        common_env = {
            "DATA_BUCKET": data_bucket.bucket_name,
            "CACHE_TABLE": cache_table.table_name,
            "AWS_REGION_OVERRIDE": "us-west-2",
            "TEXTRACT_MODE": "auto",
            "TEXT_MIN_CHARS": "40",
            "MAX_WORKERS": "5",
            "MIN_CANDIDATE_SCORE": "0.1",
            "BEDROCK_MODEL_ID": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "BEDROCK_VISION_MODEL_ID": "us.amazon.nova-lite-v1:0",
        }

        # Shared Lambda layer with backend code + shared utilities
        # Use ** prefix so patterns match at any directory depth
        asset_excludes = [
            "**/cdk.out",
            "**/.git",
            "**/node_modules",
            "**/venv",
            "**/.venv",
            "frontend/dist",
            "test-files",
            "**/*.pyc",
            "**/__pycache__",
            "**/.DS_Store",
        ]

        backend_layer = _lambda.LayerVersion(
            self,
            "BackendLayer",
            code=_lambda.Code.from_asset(
                str(PROJECT_ROOT),
                exclude=asset_excludes,
                bundling={
                    "image": _lambda.Runtime.PYTHON_3_13.bundling_image,
                    "command": [
                        "bash",
                        "-c",
                        # Install deps, then strip packages already in Lambda runtime
                        # and test-only deps to stay under the 250MB layer limit.
                        "pip install -r backend/requirements.txt -t /asset-output/python "
                        "&& rm -rf /asset-output/python/boto3* /asset-output/python/botocore* "
                        "/asset-output/python/s3transfer* "
                        "/asset-output/python/pytest* /asset-output/python/_pytest* "
                        "/asset-output/python/pytest_mock* "
                        "&& find /asset-output/python -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; "
                        "find /asset-output/python -type d -name '*.dist-info' -exec rm -rf {} + 2>/dev/null; "
                        "cp -r backend/invoice_recon /asset-output/python/ "
                        "&& cp -r infra/lambda/shared /asset-output/python/",
                    ],
                },
            ),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_13],
            description="Backend invoice_recon + shared Lambda utilities",
        )

        # --- ParseCSV Lambda ---
        parse_csv_fn = _lambda.Function(
            self,
            "ParseCsvFn",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(
                str(PROJECT_ROOT / "infra" / "lambda" / "parse_csv")
            ),
            layers=[backend_layer],
            environment=common_env,
            memory_size=512,
            timeout=Duration.seconds(30),
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )
        data_bucket.grant_read_write(parse_csv_fn)

        # --- DiscoverPDFs Lambda ---
        discover_pdfs_fn = _lambda.Function(
            self,
            "DiscoverPdfsFn",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(
                str(PROJECT_ROOT / "infra" / "lambda" / "discover_pdfs")
            ),
            layers=[backend_layer],
            environment=common_env,
            memory_size=512,
            timeout=Duration.seconds(30),
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )
        data_bucket.grant_read(discover_pdfs_fn)

        # --- ResolvePage Lambda ---
        resolve_page_fn = _lambda.Function(
            self,
            "ResolvePageFn",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(
                str(PROJECT_ROOT / "infra" / "lambda" / "resolve_page")
            ),
            layers=[backend_layer],
            environment=common_env,
            memory_size=512,
            timeout=Duration.seconds(60),
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )
        # Bedrock + Textract permissions
        resolve_page_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=["*"],
            )
        )
        resolve_page_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["textract:AnalyzeDocument", "textract:DetectDocumentText"],
                resources=["*"],
            )
        )

        # --- ExtractEntities Lambda ---
        extract_entities_fn = _lambda.Function(
            self,
            "ExtractEntitiesFn",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(
                str(PROJECT_ROOT / "infra" / "lambda" / "extract_entities")
            ),
            layers=[backend_layer],
            environment=common_env,
            memory_size=512,
            timeout=Duration.minutes(5),
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )
        extract_entities_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=["*"],
            )
        )

        # --- IndexDocument Lambda (Docker container — PyMuPDF) ---
        index_document_fn = _lambda.DockerImageFunction(
            self,
            "IndexDocumentFn",
            code=_lambda.DockerImageCode.from_image_asset(
                str(PROJECT_ROOT),
                file="infra/lambda/index_document/Dockerfile",
                exclude=asset_excludes,
            ),
            environment={
                **common_env,
                "RESOLVE_PAGE_FN_ARN": resolve_page_fn.function_arn,
                "EXTRACT_ENTITIES_FN_ARN": extract_entities_fn.function_arn,
            },
            memory_size=2048,
            timeout=Duration.minutes(10),
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )
        data_bucket.grant_read_write(index_document_fn)
        cache_table.grant_read_write_data(index_document_fn)
        resolve_page_fn.grant_invoke(index_document_fn)
        extract_entities_fn.grant_invoke(index_document_fn)

        # --- AssembleAndMatch Lambda ---
        assemble_fn = _lambda.Function(
            self,
            "AssembleAndMatchFn",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(
                str(PROJECT_ROOT / "infra" / "lambda" / "assemble_and_match")
            ),
            layers=[backend_layer],
            environment=common_env,
            memory_size=2048,
            timeout=Duration.minutes(15),
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )
        data_bucket.grant_read_write(assemble_fn)
        cache_table.grant_read_data(assemble_fn)

        # --- Step Functions State Machine ---
        # State 1: ParseCSV
        parse_csv_task = tasks.LambdaInvoke(
            self,
            "ParseCSV",
            lambda_function=parse_csv_fn,
            payload_response_only=True,
            result_path="$.parse_result",
        )

        # State 2: DiscoverPDFs (job_id and pdf_prefix come from ParseCSV result)
        discover_pdfs_task = tasks.LambdaInvoke(
            self,
            "DiscoverPDFs",
            lambda_function=discover_pdfs_fn,
            payload=sfn.TaskInput.from_object(
                {
                    "job_id": sfn.JsonPath.string_at("$.parse_result.job_id"),
                    "bucket": sfn.JsonPath.string_at("$.bucket"),
                    "pdf_prefix": sfn.JsonPath.string_at("$.parse_result.pdf_prefix"),
                }
            ),
            payload_response_only=True,
            result_path="$.discover_result",
        )

        # State 3: Map over PDFs (IndexDocument per PDF)
        index_document_task = tasks.LambdaInvoke(
            self,
            "IndexDocument",
            lambda_function=index_document_fn,
            payload=sfn.TaskInput.from_object(
                {
                    "job_id": sfn.JsonPath.string_at("$.job_id"),
                    "bucket": sfn.JsonPath.string_at("$.bucket"),
                    "doc_id": sfn.JsonPath.string_at("$.doc_id"),
                    "budget_item": sfn.JsonPath.string_at("$.budget_item"),
                    "s3_key": sfn.JsonPath.string_at("$.s3_key"),
                    "resolve_page_fn_arn": resolve_page_fn.function_arn,
                    "extract_entities_fn_arn": extract_entities_fn.function_arn,
                }
            ),
            payload_response_only=True,
        )

        map_pdfs = sfn.Map(
            self,
            "MapPDFs",
            items_path="$.discover_result.pdf_mappings",
            parameters={
                "job_id.$": "$.parse_result.job_id",
                "bucket.$": "$.bucket",
                "doc_id.$": "$$.Map.Item.Value.doc_id",
                "budget_item.$": "$$.Map.Item.Value.budget_item",
                "s3_key.$": "$$.Map.Item.Value.s3_key",
            },
            max_concurrency=5,
            result_path="$.map_result",
        )
        map_pdfs.iterator(index_document_task)

        # State 4: AssembleAndMatch
        assemble_task = tasks.LambdaInvoke(
            self,
            "AssembleAndMatch",
            lambda_function=assemble_fn,
            payload=sfn.TaskInput.from_object(
                {
                    "job_id": sfn.JsonPath.string_at("$.parse_result.job_id"),
                    "bucket": sfn.JsonPath.string_at("$.bucket"),
                    "line_items_key": sfn.JsonPath.string_at(
                        "$.parse_result.line_items_key"
                    ),
                    "pdf_mappings": sfn.JsonPath.object_at(
                        "$.discover_result.pdf_mappings"
                    ),
                }
            ),
            payload_response_only=True,
        )

        # Chain the states
        definition = (
            parse_csv_task.next(discover_pdfs_task).next(map_pdfs).next(assemble_task)
        )

        state_machine = sfn.StateMachine(
            self,
            "InvoiceProcessingPipeline",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.hours(1),
            logs=sfn.LogOptions(
                destination=logs.LogGroup(self, "StateMachineLogs"),
                level=sfn.LogLevel.ERROR,
            ),
        )

        # --- EventBridge Rule: S3 PutObject on invoice.csv triggers pipeline ---
        rule = events.Rule(
            self,
            "CsvUploadTrigger",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [data_bucket.bucket_name]},
                    "object": {"key": [{"suffix": "/invoice.csv"}]},
                },
            ),
        )

        # Transform the S3 event into Step Functions input
        rule.add_target(
            targets.SfnStateMachine(
                state_machine,
                input=events.RuleTargetInput.from_object(
                    {
                        "job_id": events.EventField.from_path("$.detail.object.key"),
                        "bucket": data_bucket.bucket_name,
                        "csv_key": events.EventField.from_path("$.detail.object.key"),
                        "pdf_prefix": "PLACEHOLDER",  # derived in Lambda from csv_key
                    }
                ),
            )
        )

        # --- API Gateway: Upload presigned URLs + Job status ---
        api = apigw.RestApi(
            self,
            "InvoiceProcessorApi",
            rest_api_name="InvoiceProcessorApi",
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=apigw.Cors.ALL_METHODS,
            ),
        )

        # POST /api/upload/start — returns presigned URLs
        upload_start_fn = _lambda.Function(
            self,
            "UploadStartFn",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_inline(self._upload_start_code()),
            environment={
                "DATA_BUCKET": data_bucket.bucket_name,
            },
            timeout=Duration.seconds(10),
        )
        data_bucket.grant_put(upload_start_fn)

        api_resource = api.root.add_resource("api")

        api_upload = api_resource.add_resource("upload")
        api_upload.add_resource("start").add_method(
            "POST",
            apigw.LambdaIntegration(upload_start_fn),
        )

        # GET /api/jobs/{jobId}/status — check Step Functions status
        job_status_fn = _lambda.Function(
            self,
            "JobStatusFn",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_inline(self._job_status_code()),
            environment={
                "STATE_MACHINE_ARN": state_machine.state_machine_arn,
            },
            timeout=Duration.seconds(10),
        )
        state_machine.grant_read(job_status_fn)

        api_jobs = api_resource.add_resource("jobs")
        api_job = api_jobs.add_resource("{jobId}")
        api_job.add_resource("status").add_method(
            "GET",
            apigw.LambdaIntegration(job_status_fn),
        )

        self.api_url = api.url

    @staticmethod
    def _upload_start_code() -> str:
        """Inline code for upload start Lambda."""
        return """
import json
import os
import uuid
import boto3
from botocore.config import Config

REGION = os.environ.get("AWS_REGION", "us-west-2")
s3 = boto3.client(
    "s3",
    region_name=REGION,
    endpoint_url=f"https://s3.{REGION}.amazonaws.com",
    config=Config(signature_version="s3v4"),
)
BUCKET = os.environ["DATA_BUCKET"]

def handler(event, context):
    body = json.loads(event.get("body", "{}"))

    # New format: { pdf_files: { slug: [rel_path, ...] } }
    # Legacy format: { pdf_filenames: [filename, ...] }
    pdf_files = body.get("pdf_files", {})
    if not pdf_files and "pdf_filenames" in body:
        for filename in body["pdf_filenames"]:
            pdf_files[filename] = [filename]

    job_id = str(uuid.uuid4())
    prefix = f"uploads/{job_id}"

    urls = {}

    # CSV presigned URL
    csv_key = f"{prefix}/invoice.csv"
    urls["csv"] = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": BUCKET, "Key": csv_key},
        ExpiresIn=3600,
    )

    # PDF presigned URLs keyed by relative path
    urls["pdfs"] = {}
    for slug, rel_paths in pdf_files.items():
        for rel_path in rel_paths:
            pdf_key = f"{prefix}/pdf/{rel_path}"
            urls["pdfs"][rel_path] = s3.generate_presigned_url(
                "put_object",
                Params={"Bucket": BUCKET, "Key": pdf_key},
                ExpiresIn=3600,
            )

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"job_id": job_id, "presigned_urls": urls}),
    }
"""

    @staticmethod
    def _job_status_code() -> str:
        """Inline code for job status Lambda."""
        return """
import json
import os
import boto3

sfn = boto3.client("stepfunctions")
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]

def handler(event, context):
    job_id = event["pathParameters"]["jobId"]

    # List executions and find one matching this job_id
    resp = sfn.list_executions(
        stateMachineArn=STATE_MACHINE_ARN,
        maxResults=50,
    )

    status = "NOT_FOUND"
    for execution in resp.get("executions", []):
        # Check if execution input contains this job_id
        detail = sfn.describe_execution(
            executionArn=execution["executionArn"]
        )
        try:
            input_data = json.loads(detail.get("input", "{}"))
            if job_id in input_data.get("job_id", "") or job_id in input_data.get("csv_key", ""):
                status = detail["status"]
                break
        except (json.JSONDecodeError, KeyError):
            continue

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"job_id": job_id, "status": status}),
    }
"""
