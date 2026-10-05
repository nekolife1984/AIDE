#!/usr/bin/env bash
set -euo pipefail

unit_tests_result="${UNIT_TESTS_RESULT:-missing}"
markdown_links_result="${MARKDOWN_LINKS_RESULT:-missing}"

if [[ "$unit_tests_result" != "success" || "$markdown_links_result" != "success" ]]; then
  printf 'Required validation failed: unit tests=%s, Markdown links=%s\n' \
    "$unit_tests_result" "$markdown_links_result" >&2
  exit 1
fi

printf 'All required validations passed.\n'
