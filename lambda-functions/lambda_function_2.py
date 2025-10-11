import os, json, logging, random, traceback
from urllib.parse import urlparse
import urllib3

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import get_session
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("REGION", "us-east-1")
QUEUE_URL = os.environ["QUEUE_URL"]
DDB_TABLE = os.environ["DDB_TABLE"]
DDB_PK_NAME = os.environ.get("DDB_PK_NAME", "business_id")
ES_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"].rstrip("/")
ES_INDEX = os.environ.get("ES_INDEX", "restaurants")
SUGGESTION_COUNT = int(os.environ.get("SUGGESTION_COUNT", "3"))
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "1"))
SES_SENDER = os.environ["SES_SENDER"]

sqs = boto3.client("sqs", region_name=REGION)
ddb = boto3.client("dynamodb", region_name=REGION)
ses = boto3.client("ses", region_name=REGION)
http = urllib3.PoolManager()

# ---------- Structured logging helper ----------
def log_json(level: str, **fields):
    # one-line JSON for easy screenshots & filtering in CWL
    print(json.dumps({"level": level, **fields}, ensure_ascii=False))

def os_signed_request(method: str, path: str, body: dict | None):
    url = f"{ES_ENDPOINT}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    host = urlparse(ES_ENDPOINT).netloc
    headers = {"host": host, "content-type": "application/json"} if data else {"host": host}

    session = get_session()
    creds = session.get_credentials()
    req = AWSRequest(method=method, url=url, data=data, headers=headers)
    SigV4Auth(creds, "es", REGION).add_auth(req)
    signed_headers = dict(req.headers.items())

    resp = http.request(method, url, body=data, headers=signed_headers, timeout=urllib3.Timeout(connect=3.0, read=8.0))
    if resp.status >= 400:
        raise RuntimeError(f"OpenSearch {resp.status}: {resp.data[:200]}")
    return json.loads(resp.data.decode("utf-8")) if resp.data else {}

def get_random_restaurant_ids_by_cuisine(cuisine: str, n: int) -> list[str]:
    # function_score + random_score to sample randomly by cuisine
    query = {
        "size": n,
        "query": {
            "function_score": {
                "query": { "term": { "CuisineSet": cuisine } },
                "random_score": {}  # per-request randomization
            }
        },
        "_source": ["business_id", "CuisineSet"]
    }
    res = os_signed_request("POST", f"/{ES_INDEX}/_search", query)
    total = res.get("hits", {}).get("total")
    hits = res.get("hits", {}).get("hits", [])
    logger.info("OS search: cuisine=%s size=%s total=%s hits=%s", cuisine, n, total, len(hits))
    ids = []
    for h in hits:
        src = h.get("_source", {})
        logger.info("hit _source: %s", src)  # debug; safe to remove later
        rid = src.get("business_id")
        if rid:
            ids.append(rid)
    logger.info("collected ids: %s", ids)
    return ids

def batch_get_ddb_items_by_business_ids(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    keys = [{DDB_PK_NAME: {"S": rid}} for rid in ids]
    resp = ddb.batch_get_item(RequestItems={DDB_TABLE: {"Keys": keys, "ConsistentRead": False}})
    items = resp.get("Responses", {}).get(DDB_TABLE, [])
    # Normalize into simple dicts
    def _unwrap(av):
        if "S" in av: return av["S"]
        if "N" in av: return float(av["N"])
        if "M" in av: return {k: _unwrap(v) for k, v in av["M"].items()}
        if "L" in av: return [_unwrap(v) for v in av["L"]]
        if "BOOL" in av: return av["BOOL"]
        if "SS" in av: return list(av["SS"])
        if "NS" in av: return [float(x) for x in av["NS"]]
        return None
    return [{k: _unwrap(v) for k, v in it.items()} for it in items]

def format_email(cuisine: str, party_size, dining_time, suggestions: list[dict]) -> tuple[str, str]:
    subject = f"{cuisine} restaurant suggestions"
    lines = [f"Hello! Here are my {cuisine} restaurant suggestions"
             + (f" for {party_size} people" if party_size else "")
             + (f", for {dining_time}" if dining_time else "")
             + ":"]
    for i, r in enumerate(suggestions, 1):
        name = r.get("name") or r.get("Name") or r.get("business_name") or "Unknown"
        addr = r.get("address") or r.get("Address") or "Address unavailable"
        lines.append(f"{i}. {name}, located at {addr}")
    body = "\n".join(lines)
    return subject, body

def send_email(to_addr: str, subject: str, body: str):
    # Let ClientError bubble up so we can log structured info in handler
    ses.send_email(
        Source=SES_SENDER,
        Destination={"ToAddresses": [to_addr]},
        Message={
            "Subject": {"Data": subject},
            "Body": {"Text": {"Data": body}}
        }
    )

def receive_one_message():
    resp = sqs.receive_message(
        QueueUrl=QUEUE_URL,
        MaxNumberOfMessages=1,
        WaitTimeSeconds=0,
        VisibilityTimeout=45,  # retry cadence
        AttributeNames=["ApproximateReceiveCount", "SentTimestamp"]
    )
    msgs = resp.get("Messages", [])
    return msgs[0] if msgs else None

def delete_message(receipt_handle: str):
    sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt_handle)

def process_request(msg_body: dict):
    cuisine = (msg_body.get("cuisine") or "").lower()
    email = msg_body.get("email")
    party_size = msg_body.get("party_size")
    dining_time = msg_body.get("dining_time")

    if not cuisine or not email:
        raise ValueError("Missing required fields: cuisine/email")

    # 1) sample N restaurant IDs by cuisine from OpenSearch
    ids = get_random_restaurant_ids_by_cuisine(cuisine, SUGGESTION_COUNT)
    if not ids:
        raise RuntimeError(f"No restaurants found in OpenSearch for cuisine={cuisine}")

    # 2) enrich from DynamoDB
    items = batch_get_ddb_items_by_business_ids(ids)
    # keep the same order as ids
    by_id = {it.get(DDB_PK_NAME) or it.get("business_id"): it for it in items}
    ordered = [by_id.get(rid, {}) for rid in ids]

    # 3) format + 4) email via SES
    subject, body = format_email(cuisine, party_size, dining_time, ordered)
    send_email(email, subject, body)

def seed_from_ddb_to_os():
    import json
    from urllib.parse import urlparse
    import urllib3
    from botocore.session import get_session
    from botocore.awsrequest import AWSRequest
    from botocore.auth import SigV4Auth

    http = urllib3.PoolManager()

    def _send(method, path, body_bytes=None):
        url = ES_ENDPOINT + path
        headers = {"host": urlparse(ES_ENDPOINT).netloc, "content-type": "application/json"}
        req = AWSRequest(method=method, url=url, data=body_bytes, headers=headers)
        SigV4Auth(get_session().get_credentials(), "es", REGION).add_auth(req)
        return http.request(method, url, body=body_bytes, headers=dict(req.headers.items()))

    def _unwrap(av):
        if "S" in av:   return av["S"]
        if "N" in av:   return float(av["N"])
        if "SS" in av:  return list(av["SS"])
        if "NS" in av:  return [float(x) for x in av["NS"]]
        if "M" in av:   return {k: _unwrap(v) for k, v in av["M"].items()}
        if "L" in av:   return [_unwrap(v) for v in av["L"]]
        if "BOOL" in av:return av["BOOL"]
        return None

    # Scan the whole table
    items, start = [], None
    while True:
        kwargs = {"TableName": DDB_TABLE, "ProjectionExpression": "#b,#c",
                  "ExpressionAttributeNames": {"#b":"business_id","#c":"CuisineSet"}}
        if start: kwargs["ExclusiveStartKey"] = start
        resp = ddb.scan(**kwargs)
        items.extend(resp.get("Items", []))
        start = resp.get("LastEvaluatedKey")
        if not start: break

    # Bulk in batches (lowercase cuisine to be case-insensitive)
    batch, total = [], 0
    def flush():
        nonlocal batch, total
        if not batch: return
        body = ("\n".join(batch) + "\n").encode("utf-8")
        r = _send("POST", "/_bulk?refresh=wait_for", body)
        if r.status >= 300:
            raise RuntimeError(f"Bulk failed {r.status}: {r.data[:200]}")
        total += len(batch)//2
        batch = []

    for it in items:
        bid = _unwrap(it.get("business_id", {}))
        c   = _unwrap(it.get("CuisineSet", {}))
        if isinstance(c, list) and c:
            c = c[0]
        if not bid or not c: 
            continue
        doc = {"business_id": str(bid), "CuisineSet": str(c).lower()}
        batch.append(json.dumps({"index": {"_index": ES_INDEX, "_id": bid}}))
        batch.append(json.dumps(doc))
        if len(batch) >= 1000:  # 500 docs per bulk (2 lines/doc)
            flush()
    flush()
    logger.info("Seeding complete. Docs indexed: %s", total)
    return {"indexed": total}

def lambda_handler(event, context):
    # Support one-time seeding
    if isinstance(event, dict) and event.get("seed"):
        return seed_from_ddb_to_os()

    processed, errors = 0, 0
    for _ in range(MAX_PER_RUN):
        msg = receive_one_message()
        if not msg:
            break

        rh = msg["ReceiptHandle"]
        message_id = msg.get("MessageId")
        attrs = msg.get("Attributes", {})
        approx_receives = int(attrs.get("ApproximateReceiveCount", "1"))
        raw = msg.get("Body", "")

        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"raw": raw}

        # Pre-log for traceability
        log_json(
            "INFO",
            event="lf2_receive",
            requestId=context.aws_request_id,
            sqsMessageId=message_id,
            receives=approx_receives,
            body_summary={"has_email": bool(body.get("email")), "has_cuisine": bool(body.get("cuisine"))}
        )

        try:
            process_request(body)

            # SUCCESS → delete message so it doesn't retry
            delete_message(rh)
            processed += 1

            log_json(
                "INFO",
                event="send_success",
                requestId=context.aws_request_id,
                sqsMessageId=message_id,
                receives=approx_receives,
                to=body.get("email")
            )

        except ClientError as e:
            # SES / AWS client-side failures
            err_info = {
                "type": "ClientError",
                "code": e.response.get("Error", {}).get("Code"),
                "message": e.response.get("Error", {}).get("Message"),
            }
            errors += 1

            # Optional: nudge retry cadence (best-effort)
            try:
                sqs.change_message_visibility(
                    QueueUrl=QUEUE_URL,
                    ReceiptHandle=rh,
                    VisibilityTimeout=45
                )
            except Exception:
                pass

            # DO NOT delete on failure → allow SQS to retry & DLQ
            log_json(
                "ERROR",
                event="send_fail_ses",
                requestId=context.aws_request_id,
                sqsMessageId=message_id,
                receives=approx_receives,
                to=body.get("email"),
                error=err_info
            )

        except Exception as e:
            # Unknown failures — also keep message for retry/DLQ
            errors += 1
            log_json(
                "ERROR",
                event="send_fail_unknown",
                requestId=context.aws_request_id,
                sqsMessageId=message_id,
                receives=approx_receives,
                error={"type": type(e).__name__, "message": str(e), "trace": traceback.format_exc()[:800]}
            )

    return {"processed": processed, "errors": errors}



