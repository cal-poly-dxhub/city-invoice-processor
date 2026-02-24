"""Authentication stack: Cognito User Pool for application authentication."""

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_cognito as cognito,
)
from constructs import Construct


class AuthStack(Stack):
    """Cognito User Pool with Hosted UI for the Invoice Processor."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Domain prefix must be globally unique across all AWS accounts.
        # Override via CDK context: cdk deploy -c cognito_domain_prefix=my-prefix
        domain_prefix = self.node.try_get_context("cognito_domain_prefix") or "invoice-processor"

        # --- Cognito User Pool ---
        self.user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name="InvoiceProcessorUsers",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=False,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # --- Cognito Domain (Hosted UI) ---
        self.user_pool_domain = self.user_pool.add_domain(
            "CognitoDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=domain_prefix,
            ),
        )

        # --- User Pool Client ---
        # Callback URLs are placeholders; FrontendStack updates them via
        # CustomResource once the CloudFront URL is known.
        self.user_pool_client = self.user_pool.add_client(
            "WebClient",
            user_pool_client_name="InvoiceProcessorWeb",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_srp=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=["http://localhost:3000/"],
                logout_urls=["http://localhost:3000/"],
            ),
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO,
            ],
        )

        self.domain_prefix = domain_prefix

        # --- Outputs ---
        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=self.user_pool_client.user_pool_client_id)
        CfnOutput(self, "CognitoDomainPrefix", value=domain_prefix)
