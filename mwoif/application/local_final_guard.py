from __future__ import annotations

from pathlib import Path
from typing import Any

from mwoif import __version__
from mwoif.application.bootstrap import check_health
from mwoif.core.config import AppConfig
from mwoif.storage.production_schema import PRODUCTION_TABLES
from mwoif.storage.repository import HeartRepository


class LocalFinalGuard:
    """Phase 5.6 final-freeze validation for the Local/Lab build.

    This check never performs game actions and never prints secrets.  It only
    validates the frozen Local tool's effective configuration, required local
    files/private-template presence, DB/schema foundation, and production
    settings needed by the proven Heart engine.
    """

    def __init__(self, config: AppConfig, repo: HeartRepository) -> None:
        self.config = config
        self.repo = repo
        self.root = Path(config.project_root)

    @staticmethod
    def _stage(name: str, ok: bool, **detail: Any) -> dict[str, Any]:
        return {"stage": name, "ok": bool(ok), **detail}

    def _config_stage(self) -> dict[str, Any]:
        extra = self.config.extra
        required = {
            "vault_configured": bool(self.config.vault.configured),
            "db_name_present": bool(self.config.database.database),
            "game_base_url_present": bool(extra.get("MWOIF_GAME_BASE_URL")),
            "friend_grpc_target_present": bool(extra.get("MWOIF_FRIEND_GRPC_TARGET")),
            "game_version_present": bool(extra.get("MWOIF_GAME_VERSION")),
            "game_build_present": bool(extra.get("MWOIF_GAME_BUILD_VERSION")),
        }
        missing = [key for key, value in required.items() if not value]
        return self._stage(
            "CONFIG",
            not missing,
            env=self.config.env,
            checks=required,
            missing=missing,
        )

    def _files_stage(self) -> dict[str, Any]:
        required_files = (
            "run.bat",
            "run_ui.bat",
            ".gitignore",
            "database/V3_PHASE5_1_PRODUCTION_LOCAL_FOUNDATION.sql",
        )
        missing = [name for name in required_files if not (self.root / name).exists()]

        state_dir = self.root / "state"
        custom_template = str(self.config.extra.get("MWOIF_DEVPLAY_HTTP_EXACT_TEMPLATE_FILE") or "").strip()
        custom_present = False
        if custom_template:
            custom_path = Path(custom_template)
            if not custom_path.is_absolute():
                custom_path = self.root / custom_path
            custom_present = custom_path.is_file()

        sender_templates = sorted(state_dir.glob("http_login_exact_template_sender_*.private.json")) if state_dir.exists() else []
        receiver_templates = sorted(state_dir.glob("http_login_exact_template_receiver_*.private.json")) if state_dir.exists() else []
        template_ok = bool(custom_present or (sender_templates and receiver_templates))

        gitignore_text = ""
        gitignore = self.root / ".gitignore"
        if gitignore.is_file():
            try:
                gitignore_text = gitignore.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                gitignore_text = ""
        secret_rules_ok = all(rule in gitignore_text for rule in (".env", "*.private.json", "*.session.json"))

        ok = not missing and template_ok and secret_rules_ok
        return self._stage(
            "FILES",
            ok,
            missing=missing,
            sender_template_count=len(sender_templates),
            receiver_template_count=len(receiver_templates),
            custom_template_present=custom_present,
            secret_ignore_rules_ok=secret_rules_ok,
            secretOutput="NONE",
        )

    def _db_stage(self) -> dict[str, Any]:
        try:
            health = check_health(self.config, include_dashboard=False)
            prod = self.repo.check_production_tables()
            missing_prod = [str(row.get("table")) for row in prod if not row.get("exists")]
            missing_core = list(health.missing_tables)
            ok = bool(health.ok and not missing_prod)
            return self._stage(
                "DB_SCHEMA",
                ok,
                missing_core=missing_core,
                missing_production=missing_prod,
                production_expected=list(PRODUCTION_TABLES),
            )
        except Exception as exc:
            return self._stage(
                "DB_SCHEMA",
                False,
                error=type(exc).__name__,
                message=str(exc)[:240],
            )

    def _policy_stage(self) -> dict[str, Any]:
        try:
            provider = str(self.repo.get_setting("login.provider.primary") or "").strip()
            cooldown = str(self.repo.get_setting("sender.cooldown.seconds") or "").strip()
            lease = str(self.repo.get_setting("sender.lease.seconds") or "").strip()
            foundation = str(self.repo.get_setting("production.foundation.version") or "").strip()
            ok = provider == "direct-http-exact-template" and bool(cooldown) and bool(lease) and bool(foundation)
            return self._stage(
                "POLICY",
                ok,
                login_provider=provider or None,
                pair_cooldown_configured=bool(cooldown),
                sender_lease_configured=bool(lease),
                production_foundation=foundation or None,
                local_auto_disable="OFF",
                browser_during_replay="NO",
            )
        except Exception as exc:
            return self._stage(
                "POLICY",
                False,
                error=type(exc).__name__,
                message=str(exc)[:240],
            )

    def run(self) -> dict[str, Any]:
        stages = [
            self._config_stage(),
            self._files_stage(),
            self._db_stage(),
            self._policy_stage(),
        ]
        failed = [str(stage["stage"]) for stage in stages if not stage.get("ok")]
        return {
            "ok": not failed,
            "mode": "LOCAL_FINAL_FREEZE_CHECK",
            "version": __version__,
            "release": "M_WOIF_HEART_LOCAL_V1",
            "stages": stages,
            "failed_stages": failed,
            "game_actions": "NONE",
            "browser": "NO",
            "secretOutput": "NONE",
        }
