import json
import logging
import os                      # NEW
import boto3                   # NEW
from datetime import datetime  # NEW

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ------- SQS client (module scope) -------
SQS = boto3.client("sqs")
QUEUE_URL = os.environ.get("QUEUE_URL", "")

def send_to_sqs(payload: dict):
    if not QUEUE_URL:
        logger.error("QUEUE_URL env var is not set")
        return False
    try:
        resp = SQS.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(payload)
            # For FIFO queues, also pass:
            # MessageGroupId="dining-requests",
            # MessageDeduplicationId=payload.get("dedupe_id", str(time.time()))
        )
        logger.info("SQS send OK: %s", resp.get("MessageId"))
        return True
    except Exception as e:
        logger.exception("Failed to send to SQS: %s", e)
        return False


# ---------- helpers ----------
def plain_text(msg):
    return {"contentType": "PlainText", "content": msg}

def get_slots(event):
    return event["sessionState"]["intent"].get("slots", {}) or {}

def val(slots, name):
    s = slots.get(name)
    if not s: return None
    v = s.get("value") or {}
    return v.get("interpretedValue") or v.get("originalValue")

def elicit_slot(event, slot_to_elicit, message):
    intent = event["sessionState"]["intent"]
    return {
        "sessionState": {
            "dialogAction": {"type": "ElicitSlot", "slotToElicit": slot_to_elicit},
            "intent": {"name": intent["name"], "slots": intent.get("slots", {}), "state": "InProgress"},
        },
        "messages": [plain_text(message)]
    }

def delegate(event, message=None):
    intent = event["sessionState"]["intent"]
    resp = {
        "sessionState": {
            "dialogAction": {"type": "Delegate"},
            "intent": {"name": intent["name"], "slots": intent.get("slots", {}), "state": "InProgress"},
        }
    }
    if message:
        resp["messages"] = [plain_text(message)]
    return resp

def close(event, message):
    intent = event["sessionState"]["intent"]
    return {
        "sessionState": {
            "dialogAction": {"type": "Close"},
            "intent": {"name": intent["name"], "slots": intent.get("slots", {}), "state": "Fulfilled"},
        },
        "messages": [plain_text(message)]
    }

# ---------- validation for DiningSuggestionsIntent ----------
ALLOWED_CUISINES = {
    "american","indian","italian","chinese","mexican","thai","japanese",
    "mediterranean","korean","vietnamese","greek","spanish","french"
}

PROMPTS = {
    "city":   "What city or city area are you looking to dine in?",
    "cuisine":"What cuisine would you like to try?",
    "guests": "How many people are in your party?",
    "date":   "What date would you like to dine?",
    "time":   "What time would you like to dine?",
    "email":  "What's your email so I can send the suggestions?"
}

def validate(slots):
    # 1) Validate filled values; if a filled value is bad, re-elicit THAT slot
    cuisine = val(slots, "cuisine")
    if cuisine:
        if cuisine.strip().lower() not in ALLOWED_CUISINES:
            return False, "cuisine", \
                f"Sorry, I currently support {', '.join(sorted(ALLOWED_CUISINES))}. What cuisine would you like?"

    guests = val(slots, "guests")
    if guests:
        try:
            n = int(guests)
            if n < 1 or n > 20:
                return False, "guests", "Please enter a party size between 1 and 20."
        except ValueError:
            return False, "guests", "How many people are in your party? (enter a number)"

    email = val(slots, "email")
    if email and ("@" not in email or "." not in email):
        return False, "email", "That email doesn’t look right. What’s the correct email?"

    # 2) Ask for the FIRST missing slot in your desired order
    for name in ["city", "cuisine", "guests", "date", "time", "email"]:
        if not val(slots, name):
            return False, name, PROMPTS[name]

    # 3) All good
    return True, None, None


# ---------- intent handlers ----------
def handle_greeting(event, src):
    return close(event, "Hi there, how can I help?")

def handle_thankyou(event, src):
    return close(event, "You're welcome.")

def handle_dining(event, src):
    slots = get_slots(event)

    if src == "DialogCodeHook":
        ok, bad_slot, msg = validate(slots)
        if not ok:
            return elicit_slot(event, bad_slot, msg)
        return delegate(event)  # let Lex continue asking remaining slots

    # --- Fulfillment: all slots should be present ---
    city    = val(slots, "city")    or "your area"
    cuisine = val(slots, "cuisine") or "any"
    guests  = val(slots, "guests")  or "2"
    date    = val(slots, "date")    or "today"
    time    = val(slots, "time")    or "tonight"
    email   = val(slots, "email")   or "(no email)"

    # Build the message for SQS
    sqs_payload = {
        "type": "DiningSuggestionsRequest",
        "sessionId": event.get("sessionId"),
        "requestId": event.get("sessionState", {}).get("originatingRequestId"),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "city": city,
        "cuisine": cuisine,
        "guests": int(guests) if str(guests).isdigit() else guests,
        "date": date,
        "time": time,
        "email": email
    }

    queued = send_to_sqs(sqs_payload)

    if queued:
        msg = (f"Great — {guests} for {cuisine} in {city} on {date} at {time}. "
               f"I’ll email suggestions to {email}.")
    else:
        msg = ("I captured your details, but couldn’t queue the request right now. "
               "Please try again in a moment.")

    return close(event, msg)

# ---------- router ----------
def lambda_handler(event, context):
    logger.info("EVENT: %s", json.dumps(event))
    src = event.get("invocationSource", "")
    intent = event["sessionState"]["intent"]["name"]

    if intent == "GreetingIntent":
        return handle_greeting(event, src)
    if intent == "ThankYouIntent":
        return handle_thankyou(event, src)
    if intent == "DiningSuggestionsIntent":
        return handle_dining(event, src)

    return close(event, "Sorry, I didn’t understand that. How can I help?")
