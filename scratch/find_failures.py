import os
import json
from pathlib import Path

cache_dir = Path("cache")
print(f"Scanning cache directory: {cache_dir.resolve()}")

failures = 0
for f in cache_dir.glob("*.json"):
    try:
        with open(f, "r", encoding="utf-8") as file:
            data = json.load(file)
            # Check if this JSON represents a failed extraction or has error messages
            if "error" in data or data.get("candidate_name") == "Unknown (Failed to parse)":
                print(f"File: {f.name}")
                print(f"Data: {json.dumps(data, indent=2)}")
                print("-" * 50)
                failures += 1
    except Exception as e:
        print(f"Failed to read {f.name}: {e}")

print(f"Scan complete. Found {failures} failed cached extractions.")
