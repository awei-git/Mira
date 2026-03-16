To remove a duplicate post on a Substack site:

1. **List recent posts**: GET https://{site}.substack.com/api/v1/posts?limit=10
   - Response includes post objects with fields: id, title, post_date, slug

2. **Identify the duplicate**: Compare titles and publish timestamps. The duplicate is typically the later-published one with a similar or rewritten title on the same topic. Preserve the earlier/original post.

3. **Delete the duplicate**: DELETE https://{site}.substack.com/api/v1/drafts/{post_id}
   - Note: use the `/drafts/` endpoint (not `/posts/`) even for published posts — this is the correct deletion endpoint.
   - Expect HTTP 200 on success.

4. **Verify**: Re-call GET /api/v1/posts?limit=10 and confirm the count decreased by one and the original is still present.

Key gotcha: The deletion endpoint is `/api/v1/drafts/{id}`, not `/api/v1/posts/{id}`. Using the posts endpoint may not work for deletion.