"""One SQLite obligation/event store. No model calls or publication privileges."""

from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

OWNERS = {"mira-app", "mira-aws", "codex"}
STATUSES = {"queued", "claimed", "running", "succeeded", "failed", "blocked", "cancelled"}
KINDS = {"seed.draft", "agent", "ping"}


class Conflict(ValueError):
    """A stale revision, ambiguous attempt, or conflicting idempotency key."""


class Forbidden(ValueError):
    """An actor cannot perform this operation."""


def encoded(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(data.encode()) > 65_536:
        raise ValueError("payload_too_large")
    return data


def digest(body):
    return hashlib.sha256(body).hexdigest()


def safe_path(root, relative):
    root = Path(root).resolve()
    part = Path(relative)
    if part.is_absolute() or not part.parts or ".." in part.parts:
        raise ValueError("invalid_artifact_path")
    path = root / part
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError("artifact_symlink")
        current = current.parent
    return path


class Ledger:
    def __init__(self, path, artifact_root, *, clock=None):
        self.path = Path(path)
        self.root = Path(artifact_root).resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if self.path.is_symlink():
            raise ValueError("database_symlink")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS obligations (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner TEXT NOT NULL,
                  created_by TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL,
                  result_json TEXT, idempotency_key TEXT UNIQUE NOT NULL, revision INTEGER NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, lease_owner TEXT,
                  lease_expires_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                  paid_started INTEGER NOT NULL DEFAULT 0, parent_id TEXT);
                CREATE TABLE IF NOT EXISTS obligation_events (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                  obligation_id TEXT NOT NULL REFERENCES obligations(id), kind TEXT NOT NULL,
                  actor TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                  dedupe_key TEXT UNIQUE NOT NULL);
                CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON obligation_events
                  BEGIN SELECT RAISE(ABORT, 'events_are_append_only'); END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON obligation_events
                  BEGIN SELECT RAISE(ABORT, 'events_are_append_only'); END;
                CREATE TABLE IF NOT EXISTS artifacts (
                  id TEXT PRIMARY KEY, obligation_id TEXT NOT NULL REFERENCES obligations(id),
                  path TEXT NOT NULL, sha256 TEXT NOT NULL, UNIQUE(obligation_id, path));
                CREATE TABLE IF NOT EXISTS model_steps (
                  id TEXT PRIMARY KEY, obligation_id TEXT NOT NULL REFERENCES obligations(id),
                  lease_revision INTEGER NOT NULL, status TEXT NOT NULL, receipt_path TEXT NOT NULL,
                  input_sha256 TEXT NOT NULL, result_json TEXT);
            """
            )
            db.execute("INSERT OR IGNORE INTO meta VALUES ('epoch', ?)", (uuid.uuid4().hex,))
            db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', '1')")
            if db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] != "1":
                raise Conflict("unsupported_ledger_schema")
            db.commit()
        self.path.chmod(0o600)
        self._audit("Open/create schema version 1; no tasks executed")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def now(self):
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("clock_requires_timezone")
        return value.astimezone(timezone.utc).isoformat()

    def _audit(self, intention):
        path = safe_path(self.path.parent, "logs/permacomputing_audit.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                f"\n- {self.now()} | obligation ledger | Intention: {intention}; "
                f"changed: {self.path.name}; self-check: durable idempotent events, no publication; "
                "scope_adherence: issue19 tasks3/6.\n"
            )

    @contextmanager
    def transaction(self, intention):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
            self._audit(intention)
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def unpack(row):
        if row is None:
            raise KeyError("obligation_not_found")
        result = dict(row)
        for key in ("payload", "result"):
            result[key] = json.loads(result.pop(key + "_json") or "null")
        return result

    def _get(self, db, identifier):
        return self.unpack(db.execute("SELECT * FROM obligations WHERE id=?", (identifier,)).fetchone())

    def get(self, identifier):
        with closing(self.connect()) as db:
            return self._get(db, identifier)

    def by_key(self, key):
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM obligations WHERE idempotency_key=?", (key,)).fetchone()
            return self.unpack(row) if row else None

    def _event(self, db, identifier, kind, actor, payload, key):
        body = encoded(payload)
        old = db.execute("SELECT * FROM obligation_events WHERE dedupe_key=?", (key,)).fetchone()
        if old:
            if (old["obligation_id"], old["kind"], old["payload_json"]) != (identifier, kind, body):
                raise Conflict("event_idempotency_conflict")
            return old["event_id"]
        event_id = uuid.uuid4().hex
        db.execute(
            "INSERT INTO obligation_events (event_id,obligation_id,kind,actor,payload_json,created_at,dedupe_key) VALUES (?,?,?,?,?,?,?)",
            (event_id, identifier, kind, actor, body, self.now(), key),
        )
        return event_id

    def create(self, kind, owner, payload, key, actor, *, identifier=None):
        if actor not in OWNERS or owner not in OWNERS:
            raise Forbidden("unknown_actor_or_owner")
        if kind not in KINDS:  # Publication authorization is exclusively an app-side future endpoint.
            raise Forbidden("unsupported_obligation_kind")
        if not isinstance(payload, dict) or not isinstance(key, str) or not 1 <= len(key) <= 300:
            raise ValueError("invalid_payload_or_key")
        if kind == "seed.draft":
            if owner != "mira-aws" or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(payload.get("seed_id", ""))):
                raise ValueError("invalid_seed_owner_or_id")
            for name in ("seed_sha256", "policy_sha256"):
                if not re.fullmatch(r"[a-f0-9]{64}", str(payload.get(name, ""))):
                    raise ValueError("seed_input_hash_required")
            version = digest((payload["seed_sha256"] + payload["policy_sha256"]).encode())
            expected = "data/drafts/substack_en/" + payload["seed_id"] + "/" + version
            if payload.get("draft_dir") != expected:
                raise ValueError("seed_draft_directory_mismatch")
        if identifier is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier):
            raise ValueError("invalid_obligation_id")
        body = encoded(payload)
        with self.transaction("Create obligation idempotently") as db:
            old = db.execute("SELECT * FROM obligations WHERE idempotency_key=?", (key,)).fetchone()
            if old:
                if (old["kind"], old["owner"], old["created_by"], old["payload_json"]) != (kind, owner, actor, body):
                    raise Conflict("obligation_idempotency_conflict")
                return self.unpack(old)
            identifier = identifier or uuid.uuid4().hex
            status = "succeeded" if kind == "ping" else "queued"
            result = encoded("pong") if kind == "ping" else None
            now = self.now()
            db.execute(
                "INSERT INTO obligations (id,kind,owner,created_by,status,payload_json,result_json,idempotency_key,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,0,?,?)",
                (identifier, kind, owner, actor, status, body, result, key, now, now),
            )
            self._event(
                db, identifier, "obligation.created", actor, {"kind": kind, "owner": owner}, "create:" + identifier
            )
            return self._get(db, identifier)

    def listing(self, *, owner=None, status=None, after="", limit=100):
        if not 1 <= limit <= 500 or (owner and owner not in OWNERS) or (status and status not in STATUSES):
            raise ValueError("invalid_filter_or_limit")
        with closing(self.connect()) as db:
            # Immutable insertion rowid is a stable opaque pagination cursor.
            cursor = int(after or 0)
            rows = db.execute(
                "SELECT rowid AS cursor,* FROM obligations WHERE rowid>? AND (? IS NULL OR owner=?) AND (? IS NULL OR status=?) ORDER BY rowid LIMIT ?",
                (cursor, owner, owner, status, status, limit),
            ).fetchall()
            return {
                "items": [self.unpack(row) for row in rows],
                "next_cursor": str(rows[-1]["cursor"]) if rows else str(cursor),
            }

    def events(self, *, after=0, limit=100, epoch=None):
        if after < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid_event_cursor_or_limit")
        with closing(self.connect()) as db:
            current = db.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()[0]
            if epoch is not None and epoch != current:
                raise Conflict("ledger_epoch_changed")
            rows = db.execute(
                "SELECT * FROM obligation_events WHERE sequence>? ORDER BY sequence LIMIT ?", (after, limit)
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                item.pop("dedupe_key")
                items.append(item)
            return {"epoch": current, "items": items, "next_cursor": rows[-1]["sequence"] if rows else after}

    def _fence(self, row, actor, revision):
        if actor != row["owner"] or actor != row["lease_owner"]:
            raise Forbidden("lease_owner_required")
        if row["revision"] != revision or row["status"] not in {"claimed", "running"}:
            raise Conflict("stale_lease_revision")
        if not row["lease_expires_at"] or row["lease_expires_at"] <= self.now():
            raise Conflict("lease_expired_reconcile_receipt_first")

    def claim(self, identifier, actor, revision, *, lease_seconds=3600):
        if not 60 <= lease_seconds <= 10800:
            raise ValueError("invalid_lease_duration")
        with self.transaction("Claim queued obligation with fencing revision") as db:
            row = self._get(db, identifier)
            if actor != row["owner"]:
                raise Forbidden("owner_required")
            if row["status"] != "queued" or row["revision"] != revision:
                raise Conflict("claim_conflict")
            expiry = (self.clock() + timedelta(seconds=lease_seconds)).astimezone(timezone.utc).isoformat()
            db.execute(
                "UPDATE obligations SET status='claimed',revision=revision+1,attempts=attempts+1,lease_owner=?,lease_expires_at=?,updated_at=? WHERE id=?",
                (actor, expiry, self.now(), identifier),
            )
            self._event(
                db,
                identifier,
                "obligation.claimed",
                actor,
                {"revision": revision + 1},
                f"claim:{identifier}:{revision + 1}",
            )
            return self._get(db, identifier)

    def transition(self, identifier, actor, revision, status, result=None):
        with self.transaction("Transition obligation with actor and revision checks") as db:
            row = self._get(db, identifier)
            if row["revision"] != revision:
                raise Conflict("stale_revision")
            if status == "cancelled" and row["status"] == "queued":
                if actor not in {row["owner"], row["created_by"]}:
                    raise Forbidden("owner_or_creator_required")
            else:
                self._fence(row, actor, revision)
                if status not in {"running", "succeeded", "failed", "blocked"} or (
                    status == "running" and row["status"] != "claimed"
                ):
                    raise Conflict("invalid_transition")
                if row["kind"] == "seed.draft" and status == "succeeded":
                    raise Forbidden("draft_requires_artifact_completion")
            db.execute(
                "UPDATE obligations SET status=?,result_json=?,revision=revision+1,updated_at=? WHERE id=?",
                (status, encoded(result), self.now(), identifier),
            )
            self._event(
                db,
                identifier,
                "obligation." + status,
                actor,
                {"revision": revision + 1},
                f"transition:{identifier}:{revision + 1}",
            )
            return self._get(db, identifier)

    def _draft_packet(self, row):
        relative = row["payload"].get("draft_dir", "")
        if not relative.startswith("data/drafts/substack_en/"):
            raise ValueError("draft_root_required")
        packet = json.loads(safe_path(self.root, relative + "/packet.json").read_text())
        required = {"seed_id", "seed_sha256", "policy_sha256"}
        if any(packet.get(key) != row["payload"].get(key) for key in required):
            raise Conflict("draft_packet_input_mismatch")
        if packet.get("editorial_review", {}).get("pass_gate") is not True:
            raise Conflict("draft_editorial_gate_not_passed")
        expected_path = relative + "/" + packet["seed_id"] + "-draft.md"
        if packet.get("draft_path") != expected_path or packet.get("publication_gate") != "human_approval_required":
            raise Conflict("draft_packet_path_or_gate_mismatch")
        body = safe_path(self.root, expected_path).read_bytes()
        if not body or len(body) > 2_000_000 or digest(body) != packet.get("draft_sha256"):
            raise Conflict("draft_bytes_changed")
        return packet

    def _complete(self, db, row, actor):
        if db.execute(
            "SELECT 1 FROM model_steps WHERE obligation_id=? AND status!='completed'", (row["id"],)
        ).fetchone():
            raise Conflict("unfinished_model_step_requires_reconciliation")
        packet = self._draft_packet(row)
        artifact_id = digest((row["id"] + ":" + packet["draft_sha256"]).encode())
        db.execute(
            "INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?)",
            (artifact_id, row["id"], packet["draft_path"], packet["draft_sha256"]),
        )
        payload = {
            key: packet[key] for key in ("seed_id", "draft_path", "draft_sha256", "policy_sha256", "publication_gate")
        }
        payload.update(
            artifact_id=artifact_id, packet_path=row["payload"]["draft_dir"] + "/packet.json", recipient="mira-app"
        )
        payload.update(track="substack_en", kind="essay")
        event_id = self._event(db, row["id"], "draft.ready_for_signoff", actor, payload, "draft-signoff:" + row["id"])
        payload["event_id"] = event_id
        db.execute(
            "UPDATE obligations SET status='succeeded',result_json=?,revision=revision+1,updated_at=? WHERE id=?",
            (encoded(payload), self.now(), row["id"]),
        )
        return self._get(db, row["id"])

    def complete_draft(self, identifier, actor, revision):
        with self.transaction("Commit draft artifact and signoff event atomically") as db:
            row = self._get(db, identifier)
            if row["owner"] != actor or row["kind"] != "seed.draft":
                raise Forbidden("draft_owner_required")
            if row["status"] == "succeeded":
                packet = self._draft_packet(row)
                if packet["draft_sha256"] != row["result"]["draft_sha256"]:
                    raise Conflict("completed_draft_version_changed")
                return row
            self._fence(row, actor, revision)
            return self._complete(db, row, actor)

    def reconcile(self, identifier, actor, revision):
        """Never calls models. Receipts either finish, safely requeue, or block."""
        with self.transaction("Reconcile expired draft lease from durable receipts before retry") as db:
            row = self._get(db, identifier)
            if actor != row["owner"] or row["kind"] != "seed.draft":
                raise Forbidden("draft_owner_required")
            if row["revision"] != revision or row["status"] not in {"queued", "claimed", "running", "blocked"}:
                raise Conflict("reconcile_conflict")
            if row["status"] in {"claimed", "running"} and row["lease_expires_at"] > self.now():
                raise Conflict("lease_still_live")
            relative = row["payload"]["draft_dir"]
            try:
                # A complete packet after a crash needs only an event, even if the
                # final receipt update did not happen. No writer is called here.
                if safe_path(self.root, relative + "/packet.json").exists():
                    return self._complete(db, row, actor)
                receipt_path = safe_path(self.root, relative + "/receipt.json")
                receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
                # Establish that the underlying repository is readable, not just
                # that a missing/unmounted workspace looks empty.
                safe_path(self.root, "seeds/seeds.jsonl").read_bytes()
                safe = receipt is None and not row["paid_started"] and row["status"] != "blocked"
                state, reason = (
                    ("queued", "no_paid_step_started")
                    if safe
                    else ("blocked", "attempt_requires_operator_reconciliation")
                )
            except (OSError, ValueError, KeyError):
                state, reason = "blocked", "receipt_or_artifact_unreadable"
            db.execute(
                "UPDATE obligations SET status=?,result_json=?,revision=revision+1,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE id=?",
                (state, encoded({"reason": reason}), self.now(), identifier),
            )
            self._event(
                db,
                identifier,
                "obligation.reconciled",
                actor,
                {"status": state, "reason": reason},
                f"reconcile:{identifier}:{revision + 1}",
            )
            return self._get(db, identifier)

    def begin_step(self, identifier, actor, revision, step_id, receipt_path):
        with self.transaction("Record paid model step before dispatch") as db:
            row = self._get(db, identifier)
            self._fence(row, actor, revision)
            if row["kind"] != "seed.draft":
                raise Forbidden("paid_step_requires_draft_obligation")
            if not receipt_path.startswith(row["payload"].get("draft_dir", "") + "/steps/"):
                raise ValueError("step_receipt_outside_attempt")
            receipt = json.loads(safe_path(self.root, receipt_path).read_text())
            if (
                receipt.get("obligation_id") != identifier
                or receipt.get("revision") != revision
                or receipt.get("step_id") != step_id
            ):
                raise Conflict("step_reservation_mismatch")
            if not re.fullmatch(r"[a-f0-9]{64}", receipt.get("input_sha256", "")):
                raise ValueError("step_input_hash_required")
            if db.execute("SELECT 1 FROM model_steps WHERE id=?", (step_id,)).fetchone():
                raise Conflict("paid_step_already_reserved")
            db.execute(
                "INSERT INTO model_steps VALUES (?,?,?,?,?,?,NULL)",
                (step_id, identifier, revision, "started", receipt_path, receipt["input_sha256"]),
            )
            expiry = (self.clock() + timedelta(hours=3)).astimezone(timezone.utc).isoformat()
            db.execute(
                "UPDATE obligations SET paid_started=1,lease_expires_at=?,updated_at=? WHERE id=?",
                (expiry, self.now(), identifier),
            )
            self._event(
                db,
                identifier,
                "model_step.started",
                actor,
                {"step_id": step_id, "receipt_path": receipt_path},
                "step-start:" + step_id,
            )

    def finish_step(self, identifier, actor, revision, step_id, result):
        with self.transaction("Retain model response receipt") as db:
            self._fence(self._get(db, identifier), actor, revision)
            row = db.execute(
                "SELECT * FROM model_steps WHERE id=? AND obligation_id=?", (step_id, identifier)
            ).fetchone()
            if not row or row["status"] != "started":
                raise Conflict("step_not_running")
            db.execute("UPDATE model_steps SET status='completed',result_json=? WHERE id=?", (encoded(result), step_id))
            self._event(
                db, identifier, "model_step.completed", actor, {"step_id": step_id, **result}, "step-end:" + step_id
            )

    def artifact(self, identifier):
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM artifacts WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError("artifact_not_registered")
            body = safe_path(self.root, row["path"]).read_bytes()
            if digest(body) != row["sha256"]:
                raise Conflict("artifact_bytes_changed")
            return body, row["sha256"]

    def backup(self, destination):
        destination = Path(destination)
        if destination.exists():
            raise Conflict("backup_destination_exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
        destination.chmod(0o600)
        self._audit("Created explicit SQLite backup " + destination.name)

    def import_legacy(self, task_file):
        """Explicit one-file import; retain source bytes and original task id."""
        task_file = Path(task_file)
        if task_file.is_symlink():
            raise ValueError("legacy_task_symlink")
        raw = task_file.read_bytes()
        task = json.loads(raw)
        if len(raw) > 65_536 or task_file.name != task.get("id", "") + ".json":
            raise ValueError("invalid_legacy_task")
        identifier = task["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier) or task.get("kind") not in {"agent", "ping"}:
            raise ValueError("unsupported_legacy_task")
        old_state = task.get("status")
        states = {
            "pending": "queued",
            "done": "succeeded",
            "failed": "failed",
            "cancelled": "cancelled",
            "running": "blocked",
            "working": "blocked",
            "blocked": "blocked",
        }
        if old_state not in states:
            raise ValueError("unknown_legacy_state")
        payload = {key: task.get(key, {} if key == "payload" else "") for key in ("title", "body", "payload")}
        payload["legacy_source_sha256"] = digest(raw)
        with self.transaction("Import selected legacy task, retain original file") as db:
            old = db.execute("SELECT * FROM obligations WHERE id=?", (identifier,)).fetchone()
            if old:
                if json.loads(old["payload_json"]).get("legacy_source_sha256") != digest(raw):
                    raise Conflict("legacy_import_changed")
                return self.unpack(old)
            now = self.now()
            db.execute(
                "INSERT INTO obligations (id,kind,owner,created_by,status,payload_json,result_json,idempotency_key,revision,created_at,updated_at,paid_started) VALUES (?,?,?,?,?,?,?,?,0,?,?,?)",
                (
                    identifier,
                    task["kind"],
                    "mira-aws",
                    "codex",
                    states[old_state],
                    encoded(payload),
                    encoded(task.get("result")),
                    "legacy-import:" + identifier,
                    task.get("created_at", now),
                    task.get("updated_at", now),
                    int(old_state in {"running", "working"}),
                ),
            )
            self._event(
                db,
                identifier,
                "legacy.imported",
                "codex",
                {"source_sha256": digest(raw), "old_status": old_state},
                "import:" + identifier,
            )
            return self._get(db, identifier)
