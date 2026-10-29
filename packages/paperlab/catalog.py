"""Catalog-first acquisition with immutable byte deduplication and lease fencing."""

from dataclasses import asdict
import time
import uuid
from paperlab.storage import Conflict, digest


class AcquisitionPending(Exception):
    pass


class Catalog:
    def __init__(self, store, notify=lambda *_: None):
        self.store, self.notify = store, notify

    def find(self, requirement, authorized_scopes):
        from paperlab.connectors import Candidate
        from paperlab.universe import definition

        found = []
        universe = definition(requirement)
        for scope in sorted(authorized_scopes):
            for _, entry in self.store.query(f"catalog#{scope}", "version#", limit=200):
                candidate = Candidate(**entry["candidate"])
                requested = candidate.request
                if candidate.scope != scope or candidate.kind != "stock_returns":
                    continue
                if requirement.kind not in {"prices", "returns"}:
                    continue
                if requirement.symbols and not set(requirement.symbols) <= set(requested.get("symbols", [])):
                    continue
                if not requirement.symbols and (not universe or not candidate.metadata.get("universe")):
                    continue
                if universe and candidate.metadata.get("universe", {}).get("dataset", {}).get(
                    "candidate", {}
                ).get("request", {}).get("exchanges") != sorted(set(e.upper() for e in universe.exchanges)):
                    continue
                found.append(candidate)
        return found[:8]

    def acquire(self, candidate, fetch, *, max_age=86400):
        identity = digest(
            {"connector": candidate.connector, "request": candidate.request, "scope": candidate.scope}
        )
        pk = f"catalog#{candidate.scope}"
        cached, _ = self.store.get(pk, f"request#{identity}")
        if cached and time.time() - cached["retrieved_at"] < max_age:
            self.notify("cache_hit", {"candidate_id": candidate.id, "version": cached["version"]})
            return cached
        # Compatible supersets reuse objects; immutable run manifests record the exact requested slice.
        for _, entry in self.store.query(pk, "version#", limit=200):
            prior = entry["candidate"]
            old, requested = prior["request"], candidate.request

            def dimensions(r):
                return {k: v for k, v in r.items() if k not in {"start", "end", "symbols"}}

            if (
                candidate.kind == "stock_returns"
                and prior["connector"] == candidate.connector
                and dimensions(old) == dimensions(requested)
                and set(requested.get("symbols", [])) <= set(old.get("symbols", []))
                and old.get("start", "9999") <= requested.get("start", "")
                and old.get("end", "") >= requested.get("end", "9999")
                and time.time() - entry["retrieved_at"] < max_age
            ):
                self.notify(
                    "cache_hit",
                    {
                        "candidate_id": candidate.id,
                        "version": entry["version"],
                        "match": "compatible_superset",
                    },
                )
                return {**entry, "slice": requested}
        lease_key = f"lease#{identity}"
        lease, revision = self.store.get(pk, lease_key)
        if lease and lease["until"] > time.time():
            raise AcquisitionPending("Another acquisition is in progress")
        owner = uuid.uuid4().hex
        try:
            self.store.put(pk, lease_key, {"owner": owner, "until": time.time() + 300}, revision)
        except Conflict as exc:
            raise AcquisitionPending() from exc
        try:
            self.notify("fetch_started", {"candidate_id": candidate.id, "connector": candidate.connector})
            body, metadata = fetch(candidate)
            if len(body) > 100 * 1024 * 1024:
                raise ValueError("Dataset exceeds acquisition size limit")
            content = digest(body)
            # Never deduplicate across incompatible entitlement scopes.
            scope_hash = digest(candidate.scope)
            key = f"shared-data/{scope_hash}/objects/sha256/{content}"
            self.store.blob_put(key, body, "application/octet-stream")
            version = digest({"object": key, "metadata": metadata})
            result = {
                "version": version,
                "key": key,
                "checksum": content,
                "metadata": metadata,
                "candidate": asdict(candidate),
                "retrieved_at": time.time(),
                "scope": candidate.scope,
            }
            current, fence = self.store.get(pk, lease_key)
            if not current or current["owner"] != owner or current["until"] <= time.time():
                raise AcquisitionPending("Acquisition lease expired")
            _, index_rev = self.store.get(pk, f"request#{identity}")
            existing_version, _ = self.store.get(pk, f"version#{version}")
            version_write = [] if existing_version else [(pk, f"version#{version}", result, 0)]
            # Commit catalog and release lease in the same transaction. Pending objects are never consumable.
            self.store.batch(
                [
                    (pk, f"request#{identity}", result, index_rev),
                    (pk, f"retrieval#{time.time_ns()}#{owner}", result, 0),
                    (pk, lease_key, {"owner": owner, "until": 0}, fence),
                ]
                + version_write
            )
            self.notify("dataset_committed", {"version": version, "metadata": metadata})
            return result
        except Exception:
            current, fence = self.store.get(pk, lease_key)
            if current and current["owner"] == owner:
                try:
                    self.store.put(pk, lease_key, {"owner": owner, "until": 0}, fence)
                except Conflict:
                    pass
            raise
