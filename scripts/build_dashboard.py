#!/usr/bin/env python3
"""Build dashboard v3 HTML by injecting data from dashboard_data.json into dashboard.html."""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)


def main():
    # Step 1: Export data
    print("Step 1: Exporting data...")
    import subprocess
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, "export_data.py")],
        capture_output=True, text=True, cwd=PROJECT_DIR
    )
    print(result.stdout)
    if result.returncode != 0:
        print("ERROR:", result.stderr)
        sys.exit(1)

    # Step 2: Read generated data
    data_path = os.path.join(PROJECT_DIR, "data", "dashboard_data.json")
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data_json = json.dumps(data, ensure_ascii=False)

    # Step 3: Inject into dashboard template
    template_path = os.path.join(PROJECT_DIR, "dashboard.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    html = html.replace("{{DATA_PLACEHOLDER}}", data_json)

    # Step 4: Write output
    output_dir = os.path.join(PROJECT_DIR, "outputs")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "dashboard_v3.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    file_size = os.path.getsize(output_path)
    print(f"\nBuild complete: {output_path}")
    print(f"File size: {file_size / 1024:.1f} KB")
    print(f"Injected {len(data_json):,} chars of JSON data")


if __name__ == "__main__":
    main()
