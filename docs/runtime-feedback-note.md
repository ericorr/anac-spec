# Runtime Feedback Note

Use this note when sending the runtime draft to an engineer for feedback.

---

I’m working on a project called ANAC, but I’m trying to pressure-test the smallest useful piece before pushing the broader spec any further.

The narrow question is:

If a tool or MCP server returned structured runtime results with:

- explicit `STALE_REVISION`
- retryable vs non-retryable failures
- `action_result`
- workflow-level `outcome`

would that be useful in your stack?

This is the draft:

- [docs/specs/runtime.md](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/docs/specs/runtime.md)

The repo also has:

- runtime schemas for `action_result` and `outcome`
- mock scenarios for stale-retry and retry exhaustion
- one live Google Sheets adapter showing the same runtime shape against a real API-backed app

I’m not asking whether you’d adopt ANAC as a full protocol.

I’m asking whether this runtime layer is useful on its own.

The most helpful feedback would be:

1. Which fields are actually useful?
2. Which fields are unnecessary?
3. What is missing for a real tool/MCP server?
4. Would you adopt this as a convention without adopting a larger spec?

If you want the shortest possible path:

```bash
python3 scripts/validate_runtime_demo.py
```

That exercises the runtime shapes directly.

---
