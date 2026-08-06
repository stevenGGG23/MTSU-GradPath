#!/usr/bin/env bash
# Creates submission.zip containing files needed for grading
set -euo pipefail

OUT=submission.zip
echo "Creating $OUT..."
rm -f "$OUT"

# Files and directories to include
INCLUDE=(
  app.py
  scrape_courses.py
  requirements.txt
  README.md
  mtsugradpath
  templates
  static
  data
  tests
)

FILES=()
for p in "${INCLUDE[@]}"; do
  if [ -e "$p" ]; then
    FILES+=("$p")
  else
    echo "Warning: $p not found, skipping"
  fi
done

if [ ${#FILES[@]} -eq 0 ]; then
  echo "No files found to add. Exiting."
  exit 1
fi

zip -r "$OUT" "${FILES[@]}" -x '*__pycache__*' -x '*.pyc'
echo "Created $OUT with: ${FILES[*]}"
