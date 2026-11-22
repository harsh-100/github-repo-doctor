# Example reports

Generated with `repo-doctor analyze --path examples/sample-project --no-llm --no-tools` (built-in checks only, so
no API key or network was used). Open these in a browser or editor:

- `sample-project/index.html`: the project dashboard (history of runs)
- `sample-project/runs/<run-id>/report.html`: the full report (works offline)
- `sample-project/runs/<run-id>/report.md`: the same report in Markdown
- `sample-project/runs/<run-id>/findings.json`: the data both are built from

The sample project contains **deliberately fake** secrets to show detection and redaction; their values do not
appear in any report. The local path was replaced by `/path/to/sample-project` before publishing.
