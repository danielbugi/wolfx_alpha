import json, time, concurrent.futures
import yfinance as yf
out = []
for sym in ["BRK.B", "BF.B", "BRK/A", "BRK/B", "BRK-B", "BF-B", "BRK-A"]:
    t = time.monotonic(); rec = {"requested": sym}
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        info = ex.submit(lambda: yf.Ticker(sym).info).result(timeout=25)
        rec.update(n_keys=len(info or {}), sector=(info or {}).get("sector"), quoteType=(info or {}).get("quoteType"), sector_key_present="sector" in (info or {}))
    except Exception as e:
        rec["error_type"] = type(e).__name__
    finally:
        ex.shutdown(wait=False)
    rec["latency_ms"] = round((time.monotonic() - t) * 1000)
    out.append(rec)
print(json.dumps(out, indent=1, sort_keys=True))
