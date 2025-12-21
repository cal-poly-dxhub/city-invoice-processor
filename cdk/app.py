#!/usr/bin/env python3
import os
import aws_cdk as cdk
from stacks.lambda_stack import InvoiceProcessorStack

app = cdk.App()

account = os.environ.get('CDK_DEFAULT_ACCOUNT')
region = os.environ.get('CDK_DEFAULT_REGION', 'us-west-2')

InvoiceProcessorStack(
    app, 
    "InvoiceProcessorStack",
    env=cdk.Environment(account=account, region=region)
)

app.synth()
