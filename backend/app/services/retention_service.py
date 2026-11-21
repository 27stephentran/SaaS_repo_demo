"""Soft-delete retention cleanup service with pluggable pre-purge backup export hook."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from app.core.auth import normalize_tenant_id
from app.core.config import get_settings
from app.core.database import erp_repository
from app.models.entities import RetentionCleanupResponse

logger = logging.getLogger(__name__)


@runtime_checkable
class BackupExporter(Protocol):
    """Pluggable backup export hook invoked prior to hard-deleting expired records."""

    provider_name: str

    async def export_expired_records(
        self,
        *,
        tenant_id: str,
        cutoff_at: datetime,
        records: dict[str, list[dict[str, Any]]],
    ) -> str:
        """Archive expired soft-deleted records before purge and return a status string."""
        ...


class DeferredGoogleSheetsExporter:
    """Deferred no-op backup exporter placeholder while Google Sheets integration is on hold."""

    provider_name: str = "google_sheets_deferred"

    async def export_expired_records(
        self,
        *,
        tenant_id: str,
        cutoff_at: datetime,
        records: dict[str, list[dict[str, Any]]],
    ) -> str:
        total = sum(len(rows) for rows in records.values())
        logger.info(
            "Retention backup export deferred (provider=%s, tenant_id=%s, eligible_records=%d, cutoff=%s)",
            self.provider_name,
            tenant_id,
            total,
            cutoff_at.isoformat(),
        )
        return "deferred"


class RetentionService:
    """Orchestrates retention-window evaluation, backup hook execution, and FK-safe hard purge."""

    def __init__(self, backup_exporter: BackupExporter | None = None) -> None:
        self.backup_exporter: BackupExporter = (
            backup_exporter if backup_exporter is not None else DeferredGoogleSheetsExporter()
        )

    async def run_cleanup(
        self,
        *,
        tenant_id: str,
        dry_run: bool = True,
        retention_days_override: int | None = None,
        backup_exporter: BackupExporter | None = None,
        now: datetime | None = None,
    ) -> RetentionCleanupResponse:
        """Evaluate or purge expired soft-deleted records past the configured retention window."""
        norm_tenant = normalize_tenant_id(tenant_id)
        settings = get_settings()
        retention_days = (
            retention_days_override
            if retention_days_override is not None
            else settings.soft_delete_retention_days
        )
        ref_now = now if now is not None else datetime.now(UTC)
        if ref_now.tzinfo is None:
            ref_now = ref_now.replace(tzinfo=UTC)
        else:
            ref_now = ref_now.astimezone(UTC)

        cutoff_at = ref_now - timedelta(days=retention_days)
        batch_size = settings.retention_cleanup_batch_size
        exporter = backup_exporter if backup_exporter is not None else self.backup_exporter

        expired_records = await erp_repository.collect_expired_inactive_records(
            tenant_id=norm_tenant,
            cutoff_at=cutoff_at,
            limit=batch_size,
        )
        eligible_counts = {
            resource: len(rows) for resource, rows in expired_records.items()
        }

        if dry_run:
            stats = await erp_repository.purge_expired_inactive_records(
                tenant_id=norm_tenant,
                cutoff_at=cutoff_at,
                limit=batch_size,
                dry_run=True,
            )
            return RetentionCleanupResponse(
                tenant_id=norm_tenant,
                dry_run=True,
                retention_days=retention_days,
                cutoff_at=cutoff_at,
                backup_provider=exporter.provider_name,
                backup_status="skipped_dry_run",
                eligible_counts=eligible_counts,
                deleted_counts=stats["deleted_counts"],
                skipped_fk_protected_counts=stats["skipped_fk_protected_counts"],
            )

        backup_status = await exporter.export_expired_records(
            tenant_id=norm_tenant,
            cutoff_at=cutoff_at,
            records=expired_records,
        )
        stats = await erp_repository.purge_expired_inactive_records(
            tenant_id=norm_tenant,
            cutoff_at=cutoff_at,
            limit=batch_size,
            dry_run=False,
        )
        return RetentionCleanupResponse(
            tenant_id=norm_tenant,
            dry_run=False,
            retention_days=retention_days,
            cutoff_at=cutoff_at,
            backup_provider=exporter.provider_name,
            backup_status=backup_status,
            eligible_counts=eligible_counts,
            deleted_counts=stats["deleted_counts"],
            skipped_fk_protected_counts=stats["skipped_fk_protected_counts"],
        )


retention_service = RetentionService()
