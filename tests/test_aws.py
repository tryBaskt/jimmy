import boto3
import pytest
from moto import mock_aws
from paperlab.config import Settings
from paperlab.storage import AWSStore, Conflict, emit


@mock_aws
def test_production_storage_parity():
    region = "us-east-1"
    table = "jimmy-test-metadata"
    bucket = "jimmy-test-artifacts"
    boto3.client("dynamodb", region_name=region).create_table(
        TableName=table,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
    )
    boto3.client("s3", region_name=region).create_bucket(Bucket=bucket)
    store = AWSStore(Settings(mode="aws", table=table, bucket=bucket))
    store.put("p", "a", {"fraction": 0.1}, 0)
    assert store.get("p", "a") == ({"fraction": 0.1}, 1)
    with pytest.raises(Conflict):
        store.put("p", "a", {}, 0)
    store.blob_put("test", b"same")
    store.blob_put("test", b"same")
    assert store.blob_get("test") == b"same"
    for i in range(3):
        emit(store, "p", "test", str(i))
    rows = store.query("project#p", "event#")
    assert len(rows) == 3
    assert len(store.query("project#p", "event#", rows[0][0])) == 2
