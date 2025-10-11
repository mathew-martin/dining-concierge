import json, os, uuid, logging, boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LEX = boto3.client("lexv2-runtime")
BOT_ID = os.environ["LEX_BOT_ID"]
BOT_ALIAS_ID = os.environ["LEX_BOT_ALIAS_ID"]
BOT_LOCALE = os.environ.get("LEX_BOT_LOCALE", "en_US")

def _parse_body(event):
    body = event.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            body = {}
    return body or {}

def _get_message(event):
    body = _parse_body(event)
    qsp  = event.get("queryStringParameters") or {}
    return body.get("message") or body.get("text") or qsp.get("message")

def _get_session_id(event):
    body = _parse_body(event)
    qsp  = event.get("queryStringParameters") or {}

    # Prefer client-provided sessionId (body or query)
    sid = body.get("sessionId") or qsp.get("sessionId")
    if not sid:
        # Generate once per browser tab; front-end should persist & resend this
        sid = str(uuid.uuid4())
    return sid

def _cors():
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,X-Requested-With,Authorization,X-Api-Key",
        "Access-Control-Allow-Methods": "OPTIONS,POST,GET"
    }

def lambda_handler(event, context):
    logger.info("EVENT %s", json.dumps(event))
    msg = _get_message(event)
    if not msg:
        return {"statusCode": 400, "headers": _cors(), "body": json.dumps({"error": "No message provided"})}

    session_id = _get_session_id(event)

    lex_resp = LEX.recognize_text(
        botId=BOT_ID,
        botAliasId=BOT_ALIAS_ID,
        localeId=BOT_LOCALE,
        sessionId=session_id,
        text=msg
    )

    # Collect reply text(s) if any
    texts = [m.get("content") for m in lex_resp.get("messages", []) if m.get("content")]
    reply = " ".join(texts) if texts else "OK."

    return {
        "statusCode": 200,
        "headers": _cors(),
        "body": json.dumps({
            "message": reply,
            "sessionId": session_id,               # send back so the client can reuse it
            "lex": {
                "sessionState": lex_resp.get("sessionState", {}),
                "interpretations": lex_resp.get("interpretations", [])
            }
        })
    }
