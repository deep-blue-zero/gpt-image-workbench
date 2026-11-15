"""Durable Batch jobs and all-or-nothing cost reservations."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from app.storage.repository import BudgetError, Repository, dump, now, uid
from app.storage.security import sanitize

REMOTE_TERMINAL = {"completed", "failed", "expired", "cancelled"}


class BatchStore:
    def __init__(self, repo: Repository):
        self.repo = repo

    def create(self, plan, pricing, budget):
        exp = uid()
        groups = defaultdict(list)
        for run, estimate in zip(plan.runs, plan.estimates, strict=True):
            groups[(run.spec.model, run.spec.operation)].append((run, estimate))
        local = datetime.now(self.repo.tz)
        stamp = now()
        with self.repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if budget is not None:
                total, unknown = db.execute(
                    "SELECT COALESCE(SUM(COALESCE(calculated_cost,estimated_cost)),0), "
                    "SUM(calculated_cost IS NULL AND estimated_cost IS NULL) FROM requests WHERE local_month=?",
                    (local.strftime("%Y-%m"),),
                ).fetchone()
                if unknown or plan.total is None or total + plan.total > budget + 1e-9:
                    raise BudgetError(
                        "Spending guard: the complete Batch reservation exceeds the available local budget or has unknown costs."
                    )
            db.execute(
                "INSERT INTO experiments VALUES (?,?,?,?,?)",
                (exp, sanitize(plan.name), stamp, "batch_pending", dump([r.to_dict() for r in plan.runs])),
            )
            for (model, operation), values in groups.items():
                job_id = uid()
                endpoint = "/v1/images/edits" if operation == "edit" else "/v1/images/generations"
                db.execute(
                    "INSERT INTO batch_jobs (id,experiment_id,model,endpoint,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (job_id, exp, model, endpoint, "prepared", stamp, stamp),
                )
                for run, estimate in values:
                    db.execute(
                        "INSERT INTO requests (id,experiment_id,config_index,sample_index,created_at,local_day,local_month,status,spec_json,estimate_json,pricing_json,estimated_cost,processing_mode,batch_job_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            uid(),
                            exp,
                            run.config_index,
                            run.sample_index,
                            stamp,
                            local.strftime("%Y-%m-%d"),
                            local.strftime("%Y-%m"),
                            "batch_pending",
                            dump(run.spec.to_dict()),
                            dump(estimate.to_dict()),
                            dump(pricing),
                            estimate.usd,
                            "batch",
                            job_id,
                        ),
                    )
        return exp

    def job(self, identity):
        with self.repo.connect() as db:
            row = db.execute("SELECT * FROM batch_jobs WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ValueError("Select a saved Batch job.")
        return self.repo._unpack(dict(row))

    def check_budget(self, identity, budget):
        """Recheck a saved reservation if the guard changed before submission."""
        month = datetime.now(self.repo.tz).strftime("%Y-%m")
        with self.repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM requests WHERE batch_job_id=? AND local_month<>? LIMIT 1",
                (identity, month),
            ).fetchone():
                raise BudgetError(
                    "Spending guard: this unsubmitted reservation is from another month. "
                    "Cancel it locally and preview again to reserve against this month's budget."
                )
            total, unknown = db.execute(
                "SELECT COALESCE(SUM(COALESCE(calculated_cost,estimated_cost)),0), "
                "SUM(calculated_cost IS NULL AND estimated_cost IS NULL) FROM requests WHERE local_month=?",
                (month,),
            ).fetchone()
            if unknown or total > budget + 1e-9:
                raise BudgetError(
                    "Spending guard: recorded costs and reservations exceed the current monthly budget "
                    "or include unknown costs. Review Usage & Costs before resuming."
                )

    def requests(self, identity):
        with self.repo.connect() as db:
            rows = db.execute(
                "SELECT * FROM requests WHERE batch_job_id=? ORDER BY config_index,sample_index,id",
                (identity,),
            ).fetchall()
        return [self.repo._unpack(dict(row)) for row in rows]

    def update(self, identity, **values):
        if not values.keys() <= {
            "status",
            "remote_id",
            "input_file_id",
            "reference_files_json",
            "provider_json",
            "last_error_json",
            "import_state",
        }:
            raise ValueError("Invalid Batch update.")
        values["updated_at"] = now()
        with self.repo.connect() as db:
            db.execute(
                f"UPDATE batch_jobs SET {','.join(k + '=?' for k in values)} WHERE id=?",
                (*(dump(v) if k.endswith("_json") else v for k, v in values.items()), identity),
            )

    def request_states(self, identity, status, *, attempts=None, cost=None):
        for row in self.requests(identity):
            if row["batch_result_sha256"]:
                continue
            values = {"status": status}
            if attempts is not None:
                values["attempts"] = attempts
            if cost is not None:
                values["calculated_cost"] = cost
            self.repo.update_request(row["id"], **values)

    def reconcile_experiment(self, identity):
        job = self.job(identity)
        jobs = self.repo.batch_jobs(job["experiment_id"])
        if all(j["import_state"] == "complete" for j in jobs):
            rows = self.repo.requests(job["experiment_id"])
            state = "completed" if all(r["status"] == "succeeded" for r in rows) else "completed_with_errors"
        elif any(j["status"] in {"submission_unknown", "upload_failed", "create_failed"} for j in jobs):
            state = "batch_attention"
        elif all(j["status"] in REMOTE_TERMINAL for j in jobs):
            state = "batch_ready"
        else:
            state = "batch_pending"
        self.repo.set_experiment_status(job["experiment_id"], state)
