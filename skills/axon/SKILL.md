# Axon — Codebase Knowledge Graph

**REPLACES glob/grep/read for codebase exploration. When loaded, you MUST run axon CLI commands via bash before any file lookups. Query the knowledge graph first, read specific files only after axon tells you where to look.**

## When to Use This Skill

Load this skill when you need to:
- Understand code structure before making changes
- Find where functionality lives (search by concept, not filename)
- Assess blast radius of changes (impact analysis)
- Navigate unfamiliar codebases
- Find dead code or circular dependencies
- Compare branches at symbol level

**DO NOT use glob/grep/read first. Use axon, THEN read specific files it identifies.**

## Setup

```bash
# Check if repo is indexed
axon status

# If not indexed, run:
axon analyze

# Skip embeddings for speed (disables semantic search):
axon analyze --no-embeddings
```

**Storage:** All data at `~/.axon/repos/{repo_name}/`. Git worktrees automatically share the main repo's database.

## Decision Tree

| Situation | Command | Why |
|-----------|---------|-----|
| "Where is X implemented?" | `axon query "X"` | Hybrid search finds symbols by name/concept |
| "What calls this function?" | `axon context SYMBOL` | Shows callers, callees, community, dead status |
| "What breaks if I change this?" | `axon impact SYMBOL` | Blast radius analysis (BFS depth 3) |
| "Is this code used anywhere?" | `axon context SYMBOL` | Zero callers = dead code candidate |
| "Find all unused code" | `axon dead-code` | Lists unreachable symbols by file |
| "What changed between branches?" | `axon diff main..feature` | Symbol-level diff (added/modified/removed) |
| "Complex graph query" | `axon cypher "QUERY"` | Direct Cypher access to knowledge graph |
| "Live re-index on changes" | `axon watch` | Watch mode (main repo only, not worktrees) |

**Workflow:** `query` → `context` → `impact` → read files → make changes

## Command Reference

### `axon query QUERY`
Find symbols by name or concept.

```bash
axon query "authentication" --limit 10
```

**Output:**
```
=== auth_flow ===
  validate_user (Function) — src/auth/validate.py:12
  login_handler (Function) — src/routes/auth.py:45

Next: Use `axon context <symbol>` for the full picture.
```

**Action:** Pick a symbol, run `axon context` on it.

---

### `axon context SYMBOL`
360-degree view: callers, callees, type refs, community, dead status.

```bash
axon context validate_user
```

**Output:**
```
validate_user (Function) — src/auth/validate.py:12
Community: auth+models

Callers (2):
  login_handler (~)    src/routes/auth.py:45
  test_validate        tests/test_auth.py:8

Callees (3):
  check_password       src/auth/utils.py:23
  get_user             src/models/user.py:67

Type refs:
  User (param)         src/models/user.py

Next: Use `axon impact validate_user` if planning changes.
```

**Confidence indicators:**
- No indicator = high confidence (≥0.9) — exact match
- `(~)` = medium (≥0.5) — method resolution
- `(?)` = low (<0.5) — fuzzy match

**Action:** If zero callers, likely dead code. If planning changes, run `axon impact`.

---

### `axon impact SYMBOL`
Blast radius — what breaks if you change this?

```bash
axon impact validate_user --depth 3
```

**Output:**
```
Impact analysis for validate_user
Depth 1 — Direct callers (will break):
  login_handler        src/routes/auth.py:45    confidence: 1.0

Depth 2 — Indirect callers (may break):
  handle_request       src/middleware.py:12

Depth 3 — Transitive (review):
  app_factory          src/app.py:5
```

**Action:** Review each depth group before making breaking changes. Depth 1 = guaranteed breakage.

---

### `axon dead-code`
List all unreachable symbols grouped by file.

```bash
axon dead-code
```

**Output:**
```
Dead Code Report (12 symbols)

  src/utils/legacy.py:
    - old_format_date (line 45)
    - deprecated_hash (line 67)
```

**Action:** Verify with `axon context` before deleting (may be public API).

---

### `axon diff BASE..HEAD`
Structural branch comparison at symbol level.

```bash
axon diff main..feature
```

**Output:**
```
Symbols added (4):
  + process_payment (Function) — src/payments/stripe.py

Symbols modified (2):
  ~ checkout_handler (Function) — src/routes/checkout.py

Symbols removed (1):
  - old_charge (Function) — src/payments/legacy.py
```

**Action:** Use for PR reviews, understanding feature scope.

---

### `axon cypher QUERY`
Execute read-only Cypher against the knowledge graph.

```bash
axon cypher "MATCH (n:Function) RETURN n.name LIMIT 5"
```

**Action:** Use for complex queries not covered by other commands.

## Cypher Queries

```cypher
-- Find all functions in a community
MATCH (n:Function)-[:MEMBER_OF]->(c:Community)
WHERE c.name CONTAINS 'auth'
RETURN n.name, n.file_path

-- Find circular dependencies
MATCH (a)-[:CALLS]->(b)-[:CALLS]->(a)
RETURN a.name, b.name

-- Find most-called functions (hotspots)
MATCH (caller)-[:CALLS]->(callee)
RETURN callee.name, count(caller) AS call_count
ORDER BY call_count DESC LIMIT 10

-- Find files that always change together
MATCH (a:File)-[r:COUPLED_WITH]->(b:File)
WHERE r.strength >= 0.5
RETURN a.name, b.name, r.strength
ORDER BY r.strength DESC

-- Find orphaned classes (no callers)
MATCH (c:Class)
WHERE NOT ()-[:CALLS|INSTANTIATES]->(c)
RETURN c.name, c.file_path
```

## Worktrees & Watch Mode

**Worktrees:** Automatically use the main repo's database. No configuration needed. Run `axon status` to verify.

**Watch mode:** `axon watch` only works in the main repo, not worktrees. Use for live development. Re-indexes on file changes.

**Web UI:** `axon ui --watch` launches interactive dashboard at `localhost:8420`.

## Output Interpretation

**Confidence scores:**
- `1.0` or no indicator = exact match, guaranteed relationship
- `(~)` or `≥0.5` = likely correct, method resolution
- `(?)` or `<0.5` = fuzzy match, verify manually

**Impact depth groups:**
- Depth 1 = direct callers, WILL break
- Depth 2 = indirect callers, MAY break
- Depth 3+ = transitive, review for context

**Dead code:** Zero callers in `axon context` suggests dead code. Verify it's not a public API before deleting.

## Workflow Example

```bash
# 1. Find where authentication lives
axon query "authentication"

# 2. Understand validate_user function
axon context validate_user

# 3. Check blast radius before refactoring
axon impact validate_user --depth 3

# 4. Read specific files identified by axon
cat src/auth/validate.py
cat src/routes/auth.py

# 5. Make changes, verify with diff
axon diff main..feature
```

**Remember:** Query the graph FIRST. Read files SECOND. Axon tells you WHERE to look.
