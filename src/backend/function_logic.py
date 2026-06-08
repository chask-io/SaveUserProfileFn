import logging
import os
from typing import Any, Dict, Optional

from chask_foundation.api.api_manager import ApiManager
from chask_foundation.backend.models import OrchestrationEvent

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_users_api_manager: Optional[ApiManager] = None


def _get_users_api_manager() -> ApiManager:
    global _users_api_manager
    if _users_api_manager is not None:
        return _users_api_manager
    base_domain = os.getenv("BASE_DOMAIN")
    if not base_domain:
        raise ValueError("BASE_DOMAIN is required for chask_api calls")
    if not (base_domain.startswith("http://") or base_domain.startswith("https://")):
        base_domain = f"https://{base_domain}"
    base_url = f"{base_domain.rstrip('/')}/api/v2/users"
    manager = ApiManager(base_url=base_url)

    @manager.register("save_user_profile", "save-user-profile", "POST")
    def _save_user_profile(**payload: Any) -> Dict[str, Any]:
        return {"json": payload}

    _users_api_manager = manager
    return _users_api_manager


class FunctionBackend:
    def __init__(self, orchestration_event: OrchestrationEvent):
        self.orchestration_event = orchestration_event
        logger.info(
            "Initialized SaveUserProfileFn for org: %s",
            orchestration_event.organization.organization_id,
        )

    def process_request(self) -> str:
        extra_params = self.orchestration_event.extra_params or {}
        orchestration_session_uuid = self.orchestration_event.orchestration_session_uuid
        if not orchestration_session_uuid:
            return "No orchestration session was found in this event, so the profile was not saved."

        tool_args = self._extract_tool_args()
        profile_fields = self._provided_profile_fields(tool_args)
        if not profile_fields:
            return "No profile fields were provided to save."

        payload = {
            "orchestration_session_uuid": str(orchestration_session_uuid),
            **profile_fields,
        }
        user_uuid = extra_params.get("target_user_uuid")
        if user_uuid:
            payload["user_uuid"] = str(user_uuid)

        logger.info(
            "Saving profile fields for session %s; explicit user=%s; fields=%s",
            orchestration_session_uuid,
            bool(user_uuid),
            sorted(profile_fields.keys()),
        )
        _get_users_api_manager().call(
            "save_user_profile",
            access_token=self.orchestration_event.access_token,
            organization_id=str(self.orchestration_event.organization.organization_id),
            timeout=30,
            **payload,
        )

        return "Saved the user's profile details for this process mapping session."

    def _extract_tool_args(self) -> Dict[str, Any]:
        extra_params = self.orchestration_event.extra_params or {}
        tool_calls = extra_params.get("tool_calls") or []
        if not tool_calls:
            logger.warning("No tool calls found in orchestration event")
            return {}
        return tool_calls[0].get("args") or {}

    def _provided_profile_fields(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        allowed_fields = (
            "job_title",
            "department",
            "seniority",
            "responsibilities",
            "bio",
            "skills",
        )
        return {
            field: tool_args[field]
            for field in allowed_fields
            if tool_args.get(field) not in (None, "", [], {})
        }
