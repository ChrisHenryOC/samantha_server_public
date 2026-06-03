# GitHub CLI PR Inline Comments

## Posting Inline Comments

Use `position` (integer) not `line` or `subject_type`.
Position = line number within diff hunk, not file line number.
Calculate: position = target_line_in_diff - hunk_header_line

Use -F for integer flag in gh api.

## Responding to Comments

Fetch comment ID, use `/comments/{ID}/replies` endpoint with POST.

```bash
# Find comment ID
gh api repos/{owner}/{repo}/pulls/{PR}/comments --jq '.[] | {id, path, body: .body[:80]}'

# Reply to comment
gh api repos/{owner}/{repo}/pulls/{PR}/comments/{ID}/replies --method POST -f body="Fixed. [description]"
```
