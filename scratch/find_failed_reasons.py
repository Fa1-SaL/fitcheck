import json
from pathlib import Path

transcript_path = Path(r"C:\Users\faisa_2u122zn\.gemini\antigravity-ide\brain\301eaa16-2419-46ad-a3d5-3306108e7032\.system_generated\logs\transcript.jsonl")

if not transcript_path.exists():
    print("Transcript not found.")
    exit(1)

print("Scanning transcript for errors and pipeline failures...")

failures = []
with open(transcript_path, "r", encoding="utf-8") as f:
    for line in f:
        try:
            data = json.loads(line)
            content = str(data.get("content", ""))
            # Search for typical error keywords or log files contents in the transcript
            if "fail" in content.lower() or "error" in content.lower() or "exception" in content.lower():
                # Extract some context
                failures.append(content[:200].strip())
        except Exception:
            pass

print(f"Found {len(failures)} occurrences of errors/failures in transcript:")
# Show unique or representative error messages
for fail in list(set(failures))[:20]:
    print(f"- {fail}")
