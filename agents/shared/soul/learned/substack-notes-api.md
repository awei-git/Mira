---
name: Substack Notes API
tags: [substack, notes, api, socialmedia]
source: reverse-engineered 2026-03-24
---

# Substack Notes API

Notes are "comments" in Substack's API. The base entity is a comment with `type: "feed"`.

## Endpoints

### Post a Note
```
POST https://{subdomain}.substack.com/api/v1/comment/feed
Body: { bodyJson: <ProseMirror doc>, tabId: "for-you", replyMinimumRole: "everyone" }
```

### Reply to a Note or Comment
Same endpoint, add `parent_id` (underscore, NOT camelCase):
```
POST https://{subdomain}.substack.com/api/v1/comment/feed
Body: { bodyJson: <ProseMirror doc>, parent_id: <comment_id>, replyMinimumRole: "everyone" }
Headers: Referer: https://substack.com/@{handle}/note/c-{parent_id}, Origin: https://substack.com
```

**Critical**: `parent_id` is the ID of the comment you're replying TO, not the root note. This determines nesting:
- Reply to note 100 → `parent_id: 100` → shows as direct reply to note
- Reply to comment 200 under note 100 → `parent_id: 200` → shows nested under comment 200

**Critical**: Referer and Origin headers are REQUIRED. Without them → 403.

### Read a Note
```
GET https://substack.com/api/v1/reader/comment/{id}
```
Returns note body, body_json, children_count, but NOT children content.

### Read Note Replies
```
GET https://substack.com/api/v1/reader/comment/{id}/replies
```
Returns `{ commentBranches: [{ comment: {...} }] }`. Each comment has `ancestor_path` showing the nesting chain.

### Edit a Note
```
POST https://{subdomain}.substack.com/api/v1/comment/{id}/edit
Body: { bodyJson: <ProseMirror doc> }
```
Note: subject to aggressive rate limiting (429).

### Delete a Note
```
DELETE https://{subdomain}.substack.com/api/v1/comment/{id}
```
**WARNING**: Deleting a note also deletes all replies/comments under it.

## Nesting Structure

```
ancestor_path: "" → root note
ancestor_path: "100" → direct reply to note 100
ancestor_path: "100.200" → reply to comment 200, which is under note 100
```

## Authentication
All endpoints use cookie auth: `Cookie: substack.sid={cookie}; connect.sid={cookie}`

## Common Mistakes
- Using `parentCommentId` (camelCase) → silently ignored, creates orphan note
- Missing Referer/Origin on reply → 403
- Replying to root note ID when you meant to reply to a specific comment → wrong nesting
- GET on `/api/v1/comment/{id}` → 404 (use `/api/v1/reader/comment/{id}` instead)
