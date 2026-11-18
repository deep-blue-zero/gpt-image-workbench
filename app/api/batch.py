"""Official Batch/Files API transport. Never falls back to direct image calls."""

from __future__ import annotations

import httpx
from openai import OpenAI

from app.api.payload import reference_file_part


class BatchAPI:
    def __init__(self, api_key, settings, client=None):
        self.client = client or OpenAI(
            api_key=api_key,
            base_url="https://api.openai.com/v1",
            max_retries=0,
            timeout=httpx.Timeout(settings.timeout_seconds, connect=20),
        )
        self.owns_client = client is None

    def close(self):
        if self.owns_client:
            self.client.close()

    def upload(self, path, purpose):
        with path.open("rb") as source:
            result = self.client.files.create(
                file=reference_file_part(source) if purpose == "vision" else source,
                purpose=purpose,
                expires_after={"anchor": "created_at", "seconds": 2592000},
            )
        return result.id

    def create(self, input_file_id, endpoint, identity, experiment_id):
        result = self.client.batches.create(
            input_file_id=input_file_id,
            endpoint=endpoint,
            completion_window="24h",
            metadata={"workbench_batch_id": identity, "workbench_experiment_id": experiment_id},
        )
        return result.model_dump(mode="json", exclude_none=True)

    def retrieve(self, identity):
        return self.client.batches.retrieve(identity).model_dump(mode="json", exclude_none=True)

    def cancel(self, identity):
        return self.client.batches.cancel(identity).model_dump(mode="json", exclude_none=True)

    def find(self, identity):
        matches = []
        for index, batch in enumerate(self.client.batches.list(limit=100)):
            if (batch.metadata or {}).get("workbench_batch_id") == identity:
                matches.append(batch.model_dump(mode="json", exclude_none=True))
            if index >= 999:
                break
        return matches

    def download(self, identity, destination):
        temporary = destination.with_suffix(".part")
        with self.client.files.with_streaming_response.content(identity) as response:
            with temporary.open("wb") as target:
                for chunk in response.iter_bytes():
                    target.write(chunk)
        temporary.replace(destination)
