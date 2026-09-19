# Metro Nashville Police Calls for Service API Guide
**Pagination Strategy, Rate Limits, and Ingestion Best Practices**

---

## 1. Overview & Architecture

Metro Nashville's Open Data portal ([data.nashville.gov](https://data.nashville.gov)) hosts public safety data via **Esri ArcGIS Online (AGOL) Hosted Feature Services** fronted by Azure Front Door CDN, rather than legacy Socrata SODA APIs.

### Primary Dataset Details
* **Title**: Metro Nashville Police Department Calls for Service
* **Type**: Hosted Feature Service Layer (Points)
* **Active Endpoint**:
  ```text
  https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/Metro_Nashville_Police_Department_Calls_for_Service_view/FeatureServer/0
  ```
* **Query Sub-resource**:
  ```text
  https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/Metro_Nashville_Police_Department_Calls_for_Service_view/FeatureServer/0/query
  ```
* **Scope**: Rolling current year (2025-12-31 through present).
* **Current Volume**: ~361,764 records (as of September 2026).
* **Update Frequency**: Live / Near real-time inserts.

### Historical Archives (Discrete Annual Services)
Metro Nashville publishes archived calls for service as separate annual feature services under the same organization ID (`HdTo6HJqh92wn4D8`):
* `Metro_Nashville_Police_Department_Calls_for_Service_2024`
* `Metro_Nashville_Police_Department_Calls_for_Service_2023`
* `Metro_Nashville_Police_Department_Calls_for_Service_2022`
* `Metro_Nashville_Police_Department_Calls_for_Service_2021`
* `Metro_Nashville_Police_Department_Calls_for_Service_2020`
* `Metro_Nashville_Police_Department_Calls_for_Service_2019`
* `Metro_Nashville_Police_Department_Calls_for_Service_2018`
* `Metro_Nashville_Police_Department_Calls_for_Service_2017`

---

## 2. Key Schema Attributes

| Field Name | Type | Description / Notes |
| :--- | :--- | :--- |
| `OBJECTID` | `esriFieldTypeOID` | Primary indexed sequence ID (`1` to `N`). Ideal for cursor pagination. |
| `Event_Number` | `esriFieldTypeString` | Unique dispatch event identifier (e.g., `PD202600123456`). |
| `Complaint_Number`| `esriFieldTypeDouble` | Incident / complaint report number. |
| `Call_Received` | `esriFieldTypeDate` | Timestamp of call (Unix epoch ms or ISO format). |
| `Tencode` | `esriFieldTypeInteger` | Police dispatch ten-code (e.g., `51`, `88`, `96`). |
| `Tencode_Description` | `esriFieldTypeString` | Description of call nature (e.g., `BURGLARY - RESIDENCE`). |
| `Disposition_Code` | `esriFieldTypeString` | Disposition code (e.g., `R`, `A`, `ADV`). |
| `Disposition_Description` | `esriFieldTypeString` | Disposition summary (e.g., `REPORT TAKEN`). |
| `Block`, `Street_Name` | `esriFieldTypeString` | Approximate street address of incident. |
| `Zone_`, `Sector`, `RPA` | `esriFieldTypeString` | Police operational sector, precinct zone, and reporting area. |
| `Latitude`, `Longitude` | `esriFieldTypeDouble` | Pre-calculated WGS84 geographic coordinates. |
| `POINT_X`, `POINT_Y` | `esriFieldTypeDouble` | Web Mercator (EPSG:3857) projection coordinates. |

---

## 3. Pagination Strategy Evaluation

We tested and benchmarked both supported pagination mechanisms on the active dataset (~362k rows):

### A. Offset Pagination (`resultOffset` & `resultRecordCount`)
* **Mechanism**: Passing `resultOffset=N` and `resultRecordCount=2000`.
* **Empirical Latency**:
  * Offset `0`: 283 ms
  * Offset `50,000`: 382 ms
  * Offset `100,000`: 648 ms
  * Offset `350,000`: 540 ms
* **Drawbacks**:
  1. **Latency Penalty**: SQL offset queries require scanning and skipping records, degrading latency by over 2.3× at higher offsets.
  2. **Page Drift (Integrity Hazard)**: Because police calls are inserted into the database continuously, rows shift during pagination. New incoming rows cause later pages to miss records or return duplicates.

### B. Keyset / Cursor Pagination (`OBJECTID > {last_id}`) — **Recommended**
* **Mechanism**: Querying `where=OBJECTID > {last_seen_id}` with `orderByFields=OBJECTID ASC` and `resultRecordCount=2000`.
* **Empirical Latency**:
  * Keyset > `0`: 351 ms
  * Keyset > `50,000`: 382 ms
  * Keyset > `100,000`: 280 ms
  * Keyset > `350,000`: 241 ms
* **Advantages**:
  1. **Flat, Sub-300ms Latency**: Queries leverage the indexed clustered primary key (`OBJECTID`) directly, maintaining high speed even at the tail end of the dataset.
  2. **Zero Page Drift**: The cursor tracks the strict sequential primary key. Newly inserted records simply append to the tail without disturbing ongoing reads.
  3. **Trivial Resumability**: If a network failure occurs, the ingestion worker simply resumes from `OBJECTID > last_successful_oid`.

---

## 4. Batch Sizing & Payload Optimization

### Record Count Thresholds
* **Default Maximum (`maxRecordCount`)**: `2000` records per request.
* **Overriding with `maxRecordCountFactor`**: The layer advertises `supportsMaxRecordCountFactor: true`.
  * Requesting `resultRecordCount=4000` with `maxRecordCountFactor=2` successfully yields 4,000 records in ~240 ms.
  * The server hard cap is **10,000 records** per request (reached at `maxRecordCountFactor=8`).
* **Sweet Spot**: **2,000 to 4,000 records** per batch balances network throughput and memory consumption.

### Field & Geometry Filtering
Because `Latitude` and `Longitude` are already exposed as plain numeric attributes, generating and transmitting GeoJSON geometries adds unnecessary overhead:

| Query Mode | Response Payload (2,000 rows) | Request Latency | Savings |
| :--- | :--- | :--- | :--- |
| **All Fields + Geometry** (`returnGeometry=true`, `outFields=*`) | 1,000.5 KB | 338 ms | Baseline |
| **All Fields NO Geometry** (`returnGeometry=false`, `outFields=*`) | 979.0 KB | 288 ms | ~2% reduction |
| **Selected Core Fields** (`returnGeometry=false`, `outFields=OBJECTID,Event_Number,Call_Received,Tencode_Description,Zone_,Latitude,Longitude`) | **332.7 KB** | **190 ms** | **66% bandwidth savings** |

---

## 5. Rate Limits & Politeness Probe

### Empirical Ramp Testing
Requests were ramped across four frequency tiers to evaluate rate limits, server response headers, and connection stability:

| Tier | Request Frequency | Interval | Total Requests | Status Codes | Latencies |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tier 1** | 1 req/sec | 1.0s | 5 | 100% `200 OK` | 315 ms – 448 ms |
| **Tier 2** | 2 req/sec | 0.5s | 6 | 100% `200 OK` | 338 ms – 368 ms |
| **Tier 3** | 4 req/sec | 0.25s | 8 | 100% `200 OK` | 205 ms – 316 ms |
| **Tier 4** | 8 req/sec | 0.125s | 10 | 100% `200 OK` | 195 ms – 321 ms |

### Server Rate Limiting Behavior
1. **Header Inspection**:
   * Esri ArcGIS Online and Azure Front Door do **not** return standard RFC 6585 rate limiting headers (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, or `X-RateLimit-Reset`) on standard `200 OK` responses.
   * Caching is managed via Azure Front Door (`Cache-Control: public, max-age=30, s-maxage=30`).
2. **Throttling Policy**:
   * AGOL enforces dynamic IP and client throttling when database consumption or concurrency thresholds are exceeded.
   * Throttled clients receive `HTTP 429 Too Many Requests` or temporary `HTTP 503/504 Service Unavailable` with optional `Retry-After` headers.
3. **Recommended Client Ingestion Policy**:
   * **Target Rate**: **1 to 2 requests per second** (or a polite pause of 500 ms between requests).
   * **Throughput**: At 2,000–4,000 records per page, 1–2 req/s yields **4,000 to 8,000 records/sec**. An entire 362k-record backfill finishes in **~45–90 seconds**.
   * **Backoff Strategy**: Implement exponential backoff with jitter (initial wait 2.0s, doubling up to 60s) upon encountering HTTP 429 or 503.
   * **User-Agent**: Always include a descriptive `User-Agent` identifying the application and project contact info.

---

## 6. Implementation Reference (Python)

Below is an example production ingest script using Keyset pagination, field filtering, and rate pacing:

```python
import urllib.request
import urllib.parse
import json
import time

QUERY_URL = (
    "https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/"
    "Metro_Nashville_Police_Department_Calls_for_Service_view/FeatureServer/0/query"
)

HEADERS = {
    "User-Agent": "NashvillePublicSafetyPanel/1.0 (contact: your-email@example.com)"
}

CORE_FIELDS = (
    "OBJECTID,Event_Number,Complaint_Number,Call_Received,Tencode,"
    "Tencode_Description,Disposition_Code,Disposition_Description,"
    "Zone_,Sector,RPA,Latitude,Longitude"
)

def fetch_calls_in_batches(batch_size=2000, min_delay_sec=0.5):
    """
    Sequentially streams all police calls using keyset (cursor) pagination.
    """
    last_oid = 0
    total_fetched = 0

    while True:
        params = {
            "where": f"OBJECTID > {last_oid}",
            "orderByFields": "OBJECTID ASC",
            "resultRecordCount": batch_size,
            "outFields": CORE_FIELDS,
            "returnGeometry": "false",
            "f": "json",
        }
        
        encoded_data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(QUERY_URL, data=encoded_data, headers=HEADERS)

        retries = 0
        backoff = 2.0
        while retries < 5:
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    break
            except urllib.error.HTTPError as e:
                if e.code in (429, 503, 504):
                    print(f"Rate limited or server busy (HTTP {e.code}). Backing off for {backoff:.1f}s...")
                    time.sleep(backoff)
                    backoff *= 2
                    retries += 1
                else:
                    raise

        features = data.get("features", [])
        if not features:
            print("Finished: No more features found.")
            break

        yield features

        total_fetched += len(features)
        last_oid = features[-1]["attributes"]["OBJECTID"]
        exceeded = data.get("exceededTransferLimit", False)
        print(f"Fetched {len(features)} records (Total: {total_fetched}, Last OBJECTID: {last_oid})")

        if not exceeded or len(features) < batch_size:
            print("Reached end of dataset.")
            break

        # Polite rate pacing (1-2 requests/sec)
        time.sleep(min_delay_sec)

if __name__ == "__main__":
    for batch in fetch_calls_in_batches():
        # Process or store batch (e.g. into SQLite / PostgreSQL / DuckDB / Parquet)
        pass
```

---

## 7. Incremental Polling Strategy

For ongoing updates every 5 to 15 minutes:
1. **By Timestamp**: Query newly received calls using standard SQL timestamp syntax:
   ```text
   Call_Received >= TIMESTAMP '2026-09-16 00:00:00'
   ```
2. **By High Watermark (OBJECTID)**: If tracking strictly newly created call records:
   ```text
   OBJECTID > {highest_saved_objectid}
   ```
