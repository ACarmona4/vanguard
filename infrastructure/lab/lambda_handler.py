"""Synthetic lab traffic: EventBridge -> producer -> SNS -> SQS -> consumer -> DynamoDB."""

import json
import os
import time
from uuid import uuid4

import boto3


def producer(event, _context):
    if event.get("simulate_error") is True:
        raise RuntimeError("Vanguard controlled lab error")
    identifier = uuid4().hex
    message = {"id": identifier, "created_at": int(time.time()), "source": "vanguard-paas-lab"}
    boto3.client("dynamodb").put_item(
        TableName=os.environ["TABLE_NAME"],
        Item={"id": {"S": "sent#" + identifier}, "status": {"S": "published"},
              "lab_expires_at": {"N": str(message["created_at"] + 86400)}},
    )
    boto3.client("sns").publish(TopicArn=os.environ["TOPIC_ARN"], Message=json.dumps(message))
    print(json.dumps({"event": "published", "id": identifier}))
    return {"id": identifier, "status": "published"}


def consumer(event, _context):
    database = boto3.client("dynamodb")
    failures = []
    for record in event.get("Records", []):
        try:
            message = json.loads(record["body"])
            # Refuse unrelated payloads; this lab processes its own synthetic events only.
            if message.get("source") != "vanguard-paas-lab" or not message.get("id"):
                raise ValueError("Unexpected lab payload")
            database.put_item(
                TableName=os.environ["TABLE_NAME"],
                Item={"id": {"S": "processed#" + message["id"]}, "status": {"S": "processed"},
                      "lab_expires_at": {"N": str(int(time.time()) + 86400)}},
            )
            print(json.dumps({"event": "processed", "id": message["id"]}))
        except Exception:
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
