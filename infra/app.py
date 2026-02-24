#!/usr/bin/env python3
"""CDK app entry point for Invoice Reconciliation serverless deployment."""

import aws_cdk as cdk

from stacks.auth_stack import AuthStack
from stacks.storage_stack import StorageStack
from stacks.processing_stack import ProcessingStack
from stacks.frontend_stack import FrontendStack

app = cdk.App()

env = cdk.Environment(
    account=app.node.try_get_context("account") or None,
    region=app.node.try_get_context("region") or "us-west-2",
)

storage = StorageStack(app, "InvoiceProcessorStorage", env=env)

auth = AuthStack(app, "InvoiceProcessorAuth", env=env)

processing = ProcessingStack(
    app,
    "InvoiceProcessorProcessing",
    data_bucket=storage.data_bucket,
    cache_table=storage.cache_table,
    user_pool=auth.user_pool,
    env=env,
)

FrontendStack(
    app,
    "InvoiceProcessorFrontend",
    data_bucket=storage.data_bucket,
    api_url=processing.api_url,
    api_rest_api_id=processing.api_rest_api_id,
    api_stage_name=processing.api_stage_name,
    user_pool=auth.user_pool,
    user_pool_client=auth.user_pool_client,
    user_pool_domain_prefix=auth.domain_prefix,
    env=env,
)

app.synth()
