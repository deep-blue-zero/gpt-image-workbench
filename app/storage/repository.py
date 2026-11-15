from __future__ import annotations

import csv
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.costs import Estimate
from app.models.spec import GenerationSpec
from app.processing.images import inspect_bytes, thumbnail
from app.processing.portable import portable_png, portable_record
from app.storage.security import sanitize

logger = logging.getLogger("workbench")


def uid() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dump(value: Any) -> str:
    return json.dumps(sanitize(value), ensure_ascii=False, separators=(",", ":"))


class BudgetError(ValueError):
    pass


class Repository:
    def __init__(self, root: Path, tz: ZoneInfo):
        self.root, self.tz = root.resolve(), tz
        self.db_path = self.root / "workbench.sqlite3"
        for folder in ("images", "thumbnails", "experiments", "previews"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
                    status TEXT NOT NULL, plan_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, experiment_id TEXT REFERENCES experiments(id),
                    config_index INTEGER, sample_index INTEGER, created_at TEXT NOT NULL,
                    local_day TEXT NOT NULL, local_month TEXT NOT NULL, status TEXT NOT NULL,
                    spec_json TEXT NOT NULL, estimate_json TEXT NOT NULL, pricing_json TEXT NOT NULL,
                    estimated_cost REAL, calculated_cost REAL, response_json TEXT, error_json TEXT,
                    api_request_id TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                    elapsed_seconds REAL, output_count INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY, request_id TEXT REFERENCES requests(id),
                    parent_image_id TEXT REFERENCES images(id), reference_ids_json TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE, thumbnail_path TEXT NOT NULL,
                    width INTEGER NOT NULL, height INTEGER NOT NULL, format TEXT NOT NULL,
                    sha256 TEXT NOT NULL, byte_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL, kind TEXT NOT NULL, metadata_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS images_request ON images(request_id);
                CREATE INDEX IF NOT EXISTS requests_experiment ON requests(experiment_id);
                CREATE TABLE IF NOT EXISTS prompts (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, text TEXT NOT NULL,
                    created_at TEXT NOT NULL, kind TEXT NOT NULL);
                PRAGMA user_version=1;
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(requests)")}
            for name, declaration in {
                "processing_mode": "TEXT NOT NULL DEFAULT 'standard'",
                "batch_job_id": "TEXT",
                "batch_result_sha256": "TEXT",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE requests ADD COLUMN {name} {declaration}")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
                    model TEXT NOT NULL, endpoint TEXT NOT NULL, status TEXT NOT NULL,
                    remote_id TEXT UNIQUE, input_file_id TEXT,
                    reference_files_json TEXT NOT NULL DEFAULT '{}', provider_json TEXT,
                    last_error_json TEXT, import_state TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS requests_batch ON requests(batch_job_id);
                PRAGMA user_version=2;
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_experiment(self, name: str, plan: list[dict[str, Any]]) -> str:
        identity = uid()
        with self.connect() as db:
            db.execute(
                "INSERT INTO experiments VALUES (?,?,?,?,?)",
                (identity, sanitize(name), now(), "queued", dump(plan)),
            )
        return identity

    def set_experiment_status(self, identity: str, status: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE experiments SET status=? WHERE id=?", (status, identity))

    def reserve(
        self,
        spec: GenerationSpec,
        estimate: Estimate,
        pricing: dict,
        experiment_id: str,
        config_index: int,
        sample_index: int,
        budget: float | None,
    ) -> str:
        identity = uid()
        local = datetime.now(self.tz)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if budget is not None:
                total = db.execute(
                    "SELECT COALESCE(SUM(COALESCE(calculated_cost,estimated_cost)),0) "
                    "FROM requests WHERE local_month=?",
                    (local.strftime("%Y-%m"),),
                ).fetchone()[0]
                unknown = db.execute(
                    "SELECT COUNT(*) FROM requests WHERE local_month=? "
                    "AND estimated_cost IS NULL AND calculated_cost IS NULL",
                    (local.strftime("%Y-%m"),),
                ).fetchone()[0]
                if unknown or estimate.usd is None:
                    raise BudgetError(
                        "Spending guard: unresolved cost exists. Review Usage & Costs before submitting."
                    )
                if total + estimate.usd > budget + 1e-9:
                    raise BudgetError(
                        f"Spending guard: ${total:.4f} recorded/reserved + ${estimate.usd:.4f} "
                        f"would exceed this month's ${budget:.2f} local budget."
                    )
            db.execute(
                """INSERT INTO requests
                (id,experiment_id,config_index,sample_index,created_at,local_day,local_month,status,
                 spec_json,estimate_json,pricing_json,estimated_cost) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identity,
                    experiment_id,
                    config_index,
                    sample_index,
                    now(),
                    local.strftime("%Y-%m-%d"),
                    local.strftime("%Y-%m"),
                    "reserved",
                    dump(spec.to_dict()),
                    dump(estimate.to_dict()),
                    dump(pricing),
                    estimate.usd,
                ),
            )
        return identity

    def update_request(self, identity: str, **values: Any) -> None:
        allowed = {
            "status",
            "calculated_cost",
            "response_json",
            "error_json",
            "api_request_id",
            "attempts",
            "elapsed_seconds",
            "output_count",
            "batch_result_sha256",
        }
        if not values.keys() <= allowed:
            raise ValueError("Invalid request update.")
        values = {k: dump(v) if k.endswith("_json") else sanitize(v) for k, v in values.items()}
        with self.connect() as db:
            db.execute(
                f"UPDATE requests SET {','.join(k + '=?' for k in values)} WHERE id=?",
                (*values.values(), identity),
            )

    def recover_interrupted(self) -> None:
        """Called only while holding the single-instance lock. Never replay requests."""
        with self.connect() as db:
            db.execute(
                "UPDATE requests SET status='interrupted' WHERE processing_mode='standard' AND status IN ('reserved','submitted')"
            )
            db.execute("UPDATE experiments SET status='interrupted' WHERE status IN ('queued','running')")
            db.execute(
                "UPDATE requests SET status='batch_unknown' WHERE batch_job_id IN (SELECT id FROM batch_jobs WHERE status='submitting')"
            )
            db.execute("UPDATE batch_jobs SET status='submission_unknown' WHERE status='submitting'")
            db.execute("UPDATE batch_jobs SET status='upload_failed' WHERE status='uploading'")

    def request(self, identity: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ValueError("Request not found.")
        return self._unpack(dict(row))

    @staticmethod
    def _unpack(row: dict[str, Any]) -> dict[str, Any]:
        for key in list(row):
            if key.endswith("_json"):
                row[key[:-5]] = json.loads(row.pop(key) or "null")
        return row

    def requests(self, experiment_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM requests"
                + (" WHERE experiment_id=?" if experiment_id else "")
                + " ORDER BY created_at DESC",
                (experiment_id,) if experiment_id else (),
            ).fetchall()
        return [self._unpack(dict(row)) for row in rows]

    def experiments(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                self._unpack(dict(row))
                for row in db.execute("SELECT * FROM experiments ORDER BY created_at DESC")
            ]

    def save_image(
        self,
        raw: bytes,
        *,
        request_id: str | None = None,
        references: tuple[str, ...] = (),
        kind: str = "generated",
        metadata: dict | None = None,
        identity: str | None = None,
    ) -> str:
        info = inspect_bytes(raw)
        supplied_identity = identity is not None
        identity = identity or uid()
        if not re.fullmatch(r"[0-9a-f]{32}", identity):
            raise ValueError("Invalid image identity.")
        if supplied_identity:
            with self.connect() as db:
                existing = db.execute("SELECT * FROM images WHERE id=?", (identity,)).fetchone()
            if existing:
                if existing["sha256"] != info.sha256 or existing["request_id"] != request_id:
                    raise ValueError("Conflicting content for an already imported batch image.")
                if (self.root / existing["path"]).read_bytes() != raw:
                    raise ValueError("Saved batch image has changed; import stopped.")
                self._prepare_portable(identity, request_id)
                return identity
        path = Path("images") / f"{identity}.{info.format}"
        thumb = Path("thumbnails") / f"{identity}.png"
        # Exclusive creation and UUID filenames guarantee source files are never replaced.
        if supplied_identity and (self.root / path).exists():
            if (self.root / path).read_bytes() != raw:
                raise ValueError("Incomplete batch image conflicts with the provider result.")
        else:
            if supplied_identity:
                temporary = (self.root / path).with_suffix(".part")
                temporary.write_bytes(raw)
                temporary.replace(self.root / path)
            else:
                with (self.root / path).open("xb") as target:
                    target.write(raw)
        with (self.root / thumb).open("wb" if supplied_identity else "xb") as target:
            target.write(thumbnail(raw))
        with self.connect() as db:
            db.execute(
                "INSERT INTO images VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    request_id,
                    references[0] if references else None,
                    dump(references),
                    path.as_posix(),
                    thumb.as_posix(),
                    info.width,
                    info.height,
                    info.format,
                    info.sha256,
                    info.byte_count,
                    now(),
                    kind,
                    dump(metadata or {}),
                ),
            )
        self._prepare_portable(identity, request_id)
        return identity

    def _prepare_portable(self, identity: str, request_id: str | None) -> None:
        if request_id:
            try:
                self.portable_image_path(identity)
            except (ValueError, OSError):
                # A secondary export failure must not discard or misclassify a paid result.
                # Gallery can retry the export; the original and its request are already saved.
                logger.warning(
                    "portable_export_failed",
                    extra={"request_id": request_id, "error_type": "portable_export"},
                )

    def portable_image_path(self, identity: str) -> Path:
        record = self.image(identity)
        payload = portable_record(record)
        folder = self.root / "portable"
        folder.mkdir(parents=True, exist_ok=True)
        if not re.fullmatch(r"[0-9a-f]{32}", identity):
            raise ValueError("Invalid image identity.")
        path = folder / f"{identity}-with-settings.png"
        if path.is_file():
            from PIL import Image

            try:
                with Image.open(path) as existing:
                    if json.loads(existing.info.get("gpt_image_workbench", "null")) == payload:
                        return path
            except (ValueError, OSError):
                pass
        raw = self.image_path(identity).read_bytes()
        output = portable_png(raw, record)
        temporary = folder / f"{identity}-{uid()}.part"
        try:
            with temporary.open("xb") as target:
                target.write(output)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def import_image(self, path: Path, raw: bytes) -> str:
        digest = inspect_bytes(raw).sha256
        with self.connect() as db:
            found = db.execute(
                "SELECT id FROM images WHERE sha256=? ORDER BY created_at LIMIT 1", (digest,)
            ).fetchone()
        if found:
            return found[0]
        return self.save_image(raw, kind="source", metadata={"original_filename": path.name})

    def image(self, identity: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM images WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ValueError("Image not found. Refresh the gallery.")
        result = self._unpack(dict(row))
        if result["request_id"]:
            result["request"] = self.request(result["request_id"])
        return result

    def generated_image_by_hash(self, digest: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT id FROM images WHERE sha256=? AND request_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                (digest,),
            ).fetchone()
        return self.image(row["id"]) if row else None

    def image_path(self, identity: str, *, thumb: bool = False) -> Path:
        record = self.image(identity)
        path = (self.root / record["thumbnail_path" if thumb else "path"]).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("Stored image is missing or outside the data directory.")
        return path

    def images(
        self,
        *,
        model: str = "",
        quality: str = "",
        experiment: str = "",
        day: str = "",
        dimensions: str = "",
        sort: str = "Newest",
    ) -> list[dict[str, Any]]:
        sql = "SELECT i.*, r.spec_json, r.experiment_id FROM images i LEFT JOIN requests r ON r.id=i.request_id WHERE 1=1"
        params: list[Any] = []
        for clause, value in (
            ("json_extract(r.spec_json,'$.model')=?", model),
            ("json_extract(r.spec_json,'$.quality')=?", quality),
            ("r.experiment_id=?", experiment),
            ("r.local_day=?", day),
            ("CAST(i.width AS TEXT)||'x'||CAST(i.height AS TEXT)=?", dimensions),
        ):
            if value:
                sql += " AND " + clause
                params.append(value)
        sorts = {
            "Newest": "i.created_at DESC",
            "Oldest": "i.created_at ASC",
            "Largest": "i.width*i.height DESC",
        }
        sql += " ORDER BY " + sorts.get(sort, sorts["Newest"])
        with self.connect() as db:
            return [self._unpack(dict(row)) for row in db.execute(sql, params)]

    def save_prompt(self, name: str, text: str, kind: str = "saved") -> str:
        if not text.strip():
            raise ValueError("Enter a prompt first.")
        identity = uid()
        with self.connect() as db:
            db.execute(
                "INSERT INTO prompts VALUES (?,?,?,?,?)",
                (identity, sanitize(name or "Untitled"), sanitize(text), now(), kind),
            )
        return identity

    def prompts(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(row) for row in db.execute("SELECT * FROM prompts ORDER BY created_at DESC LIMIT 500")
            ]

    def export(self, experiment_id: str) -> tuple[Path, Path]:
        experiment = next((x for x in self.experiments() if x["id"] == experiment_id), None)
        if not experiment:
            raise ValueError("Choose an experiment.")
        requests = self.requests(experiment_id)
        images = [self.image(x["id"]) for x in self.images(experiment=experiment_id)]
        folder = self.root / "experiments" / experiment_id
        folder.mkdir(exist_ok=True)
        json_path, csv_path = folder / "experiment.json", folder / "experiment.csv"
        json_path.write_text(
            json.dumps(
                sanitize(
                    {
                        "experiment": experiment,
                        "requests": requests,
                        "images": images,
                        "batch_jobs": self.batch_jobs(experiment_id),
                    }
                ),
                ensure_ascii=False,
                indent=2,
            ),
            "utf-8",
        )
        columns = [
            "request_id",
            "image_id",
            "status",
            "model",
            "prompt",
            "moderation",
            "quality",
            "requested_size",
            "actual_size",
            "format",
            "created_at",
            "api_request_id",
            "parent_image_id",
            "estimated_usd",
            "calculated_usd",
            "usage",
            "path",
            "error_code",
            "provider_error_type",
            "provider_error_message",
            "moderation_stage",
            "moderation_categories",
            "moderation_details",
            "processing_mode",
            "batch_job_id",
            "complexity",
        ]
        with csv_path.open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=columns)
            writer.writeheader()
            for request in requests:
                outputs = [i for i in images if i["request_id"] == request["id"]] or [{}]
                spec = GenerationSpec.from_dict(request["spec"])
                error = request.get("error") or {}
                details = error.get("moderation_details")
                details = details if isinstance(details, dict) else {}
                for item in outputs:
                    row = dict(
                        zip(
                            columns,
                            [
                                request["id"],
                                item.get("id"),
                                request["status"],
                                spec.model,
                                spec.prompt,
                                spec.moderation,
                                spec.quality,
                                spec.size,
                                f"{item['width']}x{item['height']}" if item else "",
                                item.get("format"),
                                request["created_at"],
                                request["api_request_id"],
                                item.get("parent_image_id"),
                                request["estimated_cost"],
                                request["calculated_cost"],
                                dump((request.get("response") or {}).get("usage")),
                                item.get("path"),
                                error.get("code"),
                                error.get("provider_type"),
                                error.get("provider_message"),
                                details.get("moderation_stage"),
                                dump(details.get("categories")),
                                dump(error.get("moderation_details")),
                                request["processing_mode"],
                                request["batch_job_id"],
                                spec.complexity,
                            ],
                            strict=True,
                        )
                    )
                    # Prevent spreadsheet formula execution from user prompts or names.
                    writer.writerow(
                        {
                            k: "'" + v
                            if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                            else v
                            for k, v in row.items()
                        }
                    )
        return json_path, csv_path

    def batch_jobs(self, experiment_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM batch_jobs"
                + (" WHERE experiment_id=?" if experiment_id else "")
                + " ORDER BY created_at DESC, id",
                (experiment_id,) if experiment_id else (),
            ).fetchall()
        return [self._unpack(dict(row)) for row in rows]
