# GitHub PR Review API Syntax

## Creating a Review with Comments

Use JSON input via heredoc for proper comment array passing.

```bash
gh api repos/{owner}/{repo}/pulls/{PR}/reviews --method POST --input - <<EOF
{
  "event": "COMMENT",
  "comments": [
    {"path": "src/file.py", "line": 42, "body": "Issue description"}
  ]
}
EOF
```

Event types: COMMENT, APPROVE, REQUEST_CHANGES

## Replying to Review Comments

Use `/pulls/PR_NUMBER/comments` endpoint (not `/reviews`).
`in_reply_to` field takes integer comment ID.

```bash
gh api repos/{owner}/{repo}/pulls/{PR}/comments --method POST --input - <<EOF
{
  "body": "Fixed in latest commit",
  "in_reply_to": 12345
}
EOF
```
