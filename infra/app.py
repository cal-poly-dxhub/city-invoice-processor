#!/usr/bin/env python3
"""CDK app entry point for Invoice Reconciliation serverless deployment."""

import aws_cdk as cdk

from stacks.storage_stack import StorageStack
from stacks.processing_stack import ProcessingStack
from stacks.frontend_stack import FrontendStack

app = cdk.App()

env = cdk.Environment(
    account=app.node.try_get_context("account") or None,
    region=app.node.try_get_context("region") or "us-west-2",
)

storage = StorageStack(app, "InvoiceProcessorStorage", env=env)

processing = ProcessingStack(
    app,
    "InvoiceProcessorProcessing",
    data_bucket=storage.data_bucket,
    cache_table=storage.cache_table,
    env=env,
)

FrontendStack(
    app,
    "InvoiceProcessorFrontend",
    data_bucket=storage.data_bucket,
    api_url=processing.api_url,
    env=env,
)

app.synth()
