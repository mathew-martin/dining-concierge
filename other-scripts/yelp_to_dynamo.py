import time
import requests
from decimal import Decimal
from datetime import datetime, timezone
import boto3

# ---------- CONFIG ----------
REGION = "us-east-1"
TABLE_NAME = "yelp-restaurants"

YELP_API_KEY = "YELP_API_KEY"
HEADERS = {"Authorization": f"Bearer {YELP_API_KEY}"}

LOCATION = "Manhattan"
CUISINES = ["Italian", "Chinese", "Mexican", "Indian", "Japanese"]  # ≥5 cuisines total
TARGET_PER_CUISINE = 200  # aim ~200 each
PAGE_SIZE = 50            # Yelp max 50
# ---------------------------

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)

def as_decimal(x):
    """Convert int/float/None to Decimal-compatible types for DynamoDB."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        # convert via str to preserve precision for floats
        return Decimal(str(x))
    return x

def fetch_yelp_page(cuisine: str, offset: int):
    url = "https://api.yelp.com/v3/businesses/search"
    params = {
        "term": f"{cuisine} restaurants",
        "location": LOCATION,
        "limit": PAGE_SIZE,
        "offset": offset
    }
    r = requests.get(url, headers=HEADERS, params=params, timeout=20)
    if r.status_code != 200:
        print(f"[WARN] Yelp fetch failed ({cuisine}, offset={offset}): {r.text}")
        return []
    return r.json().get("businesses", [])

def normalize_item(biz: dict, cuisine: str):
    # Required fields (assignment)
    business_id = biz.get("id")
    name = biz.get("name")
    coords = biz.get("coordinates") or {}
    loc = biz.get("location") or {}
    address = ", ".join(loc.get("display_address") or [])
    zip_code = loc.get("zip_code") or "N/A"
    reviews = biz.get("review_count", 0)
    rating = biz.get("rating", 0)

    now_iso = datetime.now(timezone.utc).isoformat()

    item = {
        "business_id": business_id,                           # PK
        "Name": name,
        "Address": address,
        "Coordinates": {
            "latitude": as_decimal(coords.get("latitude")),
            "longitude": as_decimal(coords.get("longitude")),
        },
        "NumberOfReviews": as_decimal(reviews),
        "Rating": as_decimal(rating),
        "ZipCode": zip_code,
        "insertedAtTimestamp": now_iso,                     # assignment asks to attach this when you insert
        # We'll manage cuisines as a String Set via UpdateItem (ADD) so leave it off here.
    }
    return item

def upsert_business(item: dict, cuisine: str):
    """
    Upsert with UpdateItem:
      - SET: refresh scalars (Name, Address, NumberOfReviews, Rating, ZipCode, Coordinates)
              and set insertedAtTimestamp only if not already set.
      - ADD: add cuisine to CuisineSet (String Set).
    """
    # Only 'Name' is a DynamoDB reserved word. We'll alias just that.
    expr_attr_names = {
        "#Name": "Name",
    }

    update_expr_parts = [
        "#Name = :Name",
        "Address = :Address",
        "NumberOfReviews = :NumberOfReviews",
        "Rating = :Rating",
        "ZipCode = :ZipCode",
        "Coordinates = :Coordinates",
        "insertedAtTimestamp = if_not_exists(insertedAtTimestamp, :ts)",
    ]

    expr_attr_values = {
        ":Name": item["Name"],
        ":Address": item["Address"],
        ":NumberOfReviews": item["NumberOfReviews"],
        ":Rating": item["Rating"],
        ":ZipCode": item["ZipCode"],
        ":Coordinates": item["Coordinates"],
        ":ts": item["insertedAtTimestamp"],
        ":c": set([cuisine]),  # for ADD below
    }

    update_expr = "SET " + ", ".join(update_expr_parts)
    add_expr = "ADD CuisineSet :c"

    table.update_item(
        Key={"business_id": item["business_id"]},
        UpdateExpression=f"{update_expr} {add_expr}",
        ExpressionAttributeNames=expr_attr_names,
        ExpressionAttributeValues=expr_attr_values,
        ReturnValues="NONE",
    )


def ingest():
    seen_global = set()  # avoid duplicate PUTs within the same run
    for cuisine in CUISINES:
        print(f"\n=== Ingesting cuisine: {cuisine} ===")
        fetched_ids = set()
        # Compute how many pages we need
        pages = (TARGET_PER_CUISINE + PAGE_SIZE - 1) // PAGE_SIZE

        for p in range(pages):
            offset = p * PAGE_SIZE
            businesses = fetch_yelp_page(cuisine, offset)
            if not businesses:
                break

            for biz in businesses:
                bid = biz.get("id")
                if not bid or bid in fetched_ids:
                    continue
                fetched_ids.add(bid)

                if bid in seen_global:
                    # Still add cuisine to CuisineSet for already ingested business
                    item = normalize_item(biz, cuisine)
                    upsert_business(item, cuisine)
                    continue

                item = normalize_item(biz, cuisine)
                upsert_business(item, cuisine)
                seen_global.add(bid)

            # polite delay for rate limiting
            time.sleep(0.5)

        print(f"Stored/updated {len(fetched_ids)} unique businesses for {cuisine}")

if __name__ == "__main__":
    ingest()
    print("\n✅ Done. Check DynamoDB → yelp-restaurants → Explore items.")

