"""AWS Textract fallback for scanned PDFs; preserves page-level source references."""

import time
from paperlab.storage import digest


def extract(settings, upload, notify):
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "textract",
        region_name=settings.region,
        config=Config(retries={"total_max_attempts": 3, "mode": "adaptive"}),
    )
    job = client.start_document_analysis(
        DocumentLocation={"S3Object": {"Bucket": settings.bucket, "Name": upload["key"]}},
        FeatureTypes=["TABLES"],
        ClientRequestToken=digest(
            {"bucket": settings.bucket, "key": upload["key"], "checksum": upload["checksum"]}
        ),
    )["JobId"]
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        response = client.get_document_analysis(JobId=job)
        if response["JobStatus"] == "SUCCEEDED":
            break
        if response["JobStatus"] in {"FAILED", "PARTIAL_SUCCESS"}:
            raise ValueError("OCR did not complete successfully")
        notify("ocr_progress", "OCR extraction in progress", {"job_id": job})
        time.sleep(5)
    else:
        raise ValueError("OCR time budget exhausted")
    pages = {}
    paginator = client.get_paginator("get_document_analysis")
    for batch in paginator.paginate(JobId=job):
        for block in batch.get("Blocks", []):
            if block["BlockType"] == "LINE":
                pages.setdefault(block.get("Page", 1), []).append(block["Text"])
    return [{"page": n, "text": "\n".join(lines)} for n, lines in sorted(pages.items())]
