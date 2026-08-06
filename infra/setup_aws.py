"""One-time AWS setup: bucket, lifecycle, public-access block, GitHub OIDC role.

Idempotent, safe to re-run. Run locally with admin credentials:
    pip install boto3
    python infra/setup_aws.py

Prints the role ARN and the gh commands to store repo variables.

Cost design, per docs/ROADMAP.md:
- raw/ expires after RAW_EXPIRY_DAYS, bounding that prefix by construction
- the bucket blocks all public access, so there is no public-egress cost vector
- the Actions role can touch only this bucket, nothing else in the account
- the collector itself refuses writes past its byte quota (collector/s3store.py)
"""

import json

import boto3
from botocore.exceptions import ClientError

BUCKET = "gw2-advisor-data-petrcala"
REGION = "us-east-1"
REPO = "PetrCala/gw2-advisor"
# GitHub issues ID-qualified OIDC sub claims (owner@id/repo@id); immutable ids
# pin the trust even if the repo name is ever recycled.
REPO_ID_QUALIFIED = "PetrCala@79160025/gw2-advisor@1325208858"
ROLE = "gw2-advisor-actions"
RAW_EXPIRY_DAYS = 30
OIDC_HOST = "token.actions.githubusercontent.com"


def ensure_bucket(s3):
    try:
        s3.head_bucket(Bucket=BUCKET)
        print(f"bucket {BUCKET} already exists")
    except ClientError:
        s3.create_bucket(Bucket=BUCKET)  # us-east-1 takes no LocationConstraint
        print(f"created bucket {BUCKET}")
    s3.put_public_access_block(
        Bucket=BUCKET,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_lifecycle_configuration(
        Bucket=BUCKET,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "expire-raw",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "raw/"},
                    "Expiration": {"Days": RAW_EXPIRY_DAYS},
                },
                {
                    "ID": "abort-incomplete-mpu",
                    "Status": "Enabled",
                    "Filter": {"Prefix": ""},
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
                },
            ]
        },
    )
    print(f"lifecycle set: raw/ expires after {RAW_EXPIRY_DAYS} days")


def ensure_oidc_provider(iam, account):
    arn = f"arn:aws:iam::{account}:oidc-provider/{OIDC_HOST}"
    try:
        info = iam.get_open_id_connect_provider(OpenIDConnectProviderArn=arn)
        audiences = info["ClientIDList"]
        print(f"OIDC provider already exists, audiences: {audiences}")
        if "sts.amazonaws.com" not in audiences:
            iam.add_client_id_to_open_id_connect_provider(
                OpenIDConnectProviderArn=arn, ClientID="sts.amazonaws.com"
            )
            print("added missing sts.amazonaws.com audience to existing provider")
    except iam.exceptions.NoSuchEntityException:
        iam.create_open_id_connect_provider(
            Url=f"https://{OIDC_HOST}",
            ClientIDList=["sts.amazonaws.com"],
            ThumbprintList=["6938fd4d98bab03faadb97b34396831e3780aea1"],
        )
        print("created OIDC provider for GitHub Actions")
    return arn


def ensure_role(iam, provider_arn):
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Federated": provider_arn},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {f"{OIDC_HOST}:aud": "sts.amazonaws.com"},
                    "StringLike": {
                        f"{OIDC_HOST}:sub": [
                            f"repo:{REPO}:ref:refs/heads/main",
                            f"repo:{REPO_ID_QUALIFIED}:ref:refs/heads/main",
                        ]
                    },
                },
            }
        ],
    }
    try:
        role = iam.create_role(
            RoleName=ROLE,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description=f"S3 access for {REPO} GitHub Actions",
        )
        print(f"created role {ROLE}")
    except iam.exceptions.EntityAlreadyExistsException:
        iam.update_assume_role_policy(RoleName=ROLE, PolicyDocument=json.dumps(trust))
        role = iam.get_role(RoleName=ROLE)
        print(f"role {ROLE} already exists, trust policy refreshed")

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": f"arn:aws:s3:::{BUCKET}",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": f"arn:aws:s3:::{BUCKET}/*",
            },
        ],
    }
    iam.put_role_policy(RoleName=ROLE, PolicyName="s3-data-bucket", PolicyDocument=json.dumps(policy))
    return role["Role"]["Arn"]


def main():
    account = boto3.client("sts").get_caller_identity()["Account"]
    s3 = boto3.client("s3", region_name=REGION)
    iam = boto3.client("iam")

    ensure_bucket(s3)
    provider_arn = ensure_oidc_provider(iam, account)
    role_arn = ensure_role(iam, provider_arn)

    # Show the effective trust policy so OIDC failures are diagnosable from output.
    trust_doc = iam.get_role(RoleName=ROLE)["Role"]["AssumeRolePolicyDocument"]
    print("\neffective trust policy:")
    print(json.dumps(trust_doc, indent=2))

    print("\ndone. Now store the repo variables:")
    print(f'  gh variable set AWS_ROLE_ARN --repo {REPO} --body "{role_arn}"')
    print(f'  gh variable set AWS_REGION --repo {REPO} --body "{REGION}"')
    print(f'  gh variable set S3_BUCKET --repo {REPO} --body "{BUCKET}"')


if __name__ == "__main__":
    main()
