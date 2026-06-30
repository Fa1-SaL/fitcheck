import os
from pathlib import Path

# Print all files in output/
output_dir = Path("output")
print("Files in output directory:")
for f in output_dir.iterdir():
    print(f"- {f.name} ({f.stat().st_size} bytes)")
    if f.suffix in [".txt", ".log"]:
        print("--- CONTENT ---")
        try:
            print(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Error reading: {e}")
        print("---------------")
