"""Deploy the private AWS Academy lab using the connection saved in Vanguard.

Run from the repository root: make aws-lab-plan / make aws-lab.
No credentials are written to Terraform files, user data or the agent.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/src"))


def prepare_agent():
    release = json.loads((ROOT / "observability/aws-agent-release.json").read_text())
    name = f"otelcol-contrib_{release['version']}_linux_amd64.tar.gz"
    path = ROOT / ".vanguard/agent" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == release["sha256"]:
        return path
    url = f"https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v{release['version']}/{name}"
    temporary = path.with_suffix(".download")
    try:
        with urlopen(url, timeout=120) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != release["sha256"]:
            raise RuntimeError("El checksum del agente no coincide; se canceló la instalación")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def connection_environment():
    import psycopg
    from vanguard_inventory.config import load_environment
    from vanguard_inventory.cloud_credentials import decrypt_credentials

    load_environment(ROOT / ".env")
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as connection:
        rows = connection.execute(
            "SELECT encrypted_credentials FROM cloud_connections WHERE provider = 'aws' AND enabled"
        ).fetchall()
    if len(rows) != 1:
        raise RuntimeError("El laboratorio requiere una única conexión AWS habilitada en Perfil")
    credentials = decrypt_credentials(rows[0][0])
    environment = os.environ.copy()
    for key in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        environment.pop(key, None)
    environment.update(AWS_ACCESS_KEY_ID=credentials["access_key_id"],
                       AWS_SECRET_ACCESS_KEY=credentials["secret_access_key"], AWS_DEFAULT_REGION="us-east-1")
    if credentials.get("session_token"):
        environment["AWS_SESSION_TOKEN"] = credentials["session_token"]
    return environment


def prepare_bucket(environment):
    """Create the retained package bucket without querying unsupported Object Lock."""
    import boto3
    from botocore.exceptions import ClientError

    region = environment["AWS_DEFAULT_REGION"]
    session = boto3.Session(
        aws_access_key_id=environment["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=environment["AWS_SECRET_ACCESS_KEY"],
        aws_session_token=environment.get("AWS_SESSION_TOKEN"), region_name=region,
    )
    owner = session.client("sts").get_caller_identity()["Account"]
    bucket = f"vanguard-agent-{owner}-{region}"
    s3 = session.client("s3")
    try:
        s3.head_bucket(Bucket=bucket, ExpectedBucketOwner=owner)
    except ClientError as error:
        if error.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
            raise
        arguments = {"Bucket": bucket}
        if region != "us-east-1":
            arguments["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**arguments)
    s3.put_public_access_block(Bucket=bucket, ExpectedBucketOwner=owner, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True,
        "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
    })
    s3.put_bucket_encryption(Bucket=bucket, ExpectedBucketOwner=owner, ServerSideEncryptionConfiguration={
        "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}],
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply"))
    args = parser.parse_args()
    os.chdir(ROOT)
    prepare_agent()
    environment = connection_environment()
    command = ["terraform", "-chdir=infraestructure"]
    subprocess.run([*command, "init", "-input=false"], env=environment, check=True)
    plan = "../.vanguard/aws-lab.tfplan"
    subprocess.run([*command, "plan", "-input=false", "-var=enable_aws=true", "-var=enable_gcp=false", f"-out={plan}"],
                   env=environment, check=True)
    result = subprocess.check_output([*command, "show", "-json", plan], env=environment)
    changes = json.loads(result).get("resource_changes", [])
    if any("delete" in change["change"]["actions"] for change in changes):
        raise RuntimeError("El plan incluye eliminaciones o reemplazos; revísalo antes de aplicarlo manualmente")
    if args.action == "apply":
        prepare_bucket(environment)
        subprocess.run([*command, "apply", "-input=false", plan], env=environment, check=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"No fue posible desplegar el laboratorio: {type(exc).__name__}. Revisa la conexión y el plan.", file=sys.stderr)
        raise SystemExit(1)
