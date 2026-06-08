import logging
import os
from typing import Any, Dict, Optional

from chask_foundation.api.api_manager import ApiManager
from chask_foundation.backend.models import OrchestrationEvent

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_users_api_manager: Optional[ApiManager] = None
_orchestrator_api_manager: Optional[ApiManager] = None


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


def _get_orchestrator_api_manager() -> ApiManager:
    global _orchestrator_api_manager
    if _orchestrator_api_manager is not None:
        return _orchestrator_api_manager
    base_domain = os.getenv("BASE_DOMAIN")
    if not base_domain:
        raise ValueError("BASE_DOMAIN is required for chask_api calls")
    if not (base_domain.startswith("http://") or base_domain.startswith("https://")):
        base_domain = f"https://{base_domain}"
    base_url = f"{base_domain.rstrip('/')}/api/v2/orchestrator"
    manager = ApiManager(base_url=base_url)

    @manager.register(
        "get_orchestration_session_user_data",
        "get-orchestration-session-user-data",
        "GET",
    )
    def _get_orchestration_session_user_data(
        orchestration_session_uuid: str,
        internal_orchestration_session_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "params": {
                "orchestration_session_uuid": orchestration_session_uuid,
                "internal_orchestration_session_uuid": internal_orchestration_session_uuid,
            }
        }

    _orchestrator_api_manager = manager
    return _orchestrator_api_manager


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

        user_uuid = extra_params.get("target_user_uuid") or self._resolve_user_uuid_from_session()
        if not user_uuid:
            return "No target user could be resolved from this conversation or session, so the profile was not saved."

        tool_args = self._extract_tool_args()
        profile_fields = self._provided_profile_fields(tool_args)
        if not profile_fields:
            return "No profile fields were provided to save."

        payload = {
            "user_uuid": str(user_uuid),
            "orchestration_session_uuid": str(orchestration_session_uuid),
            **profile_fields,
        }

        logger.info(
            "Saving profile fields for user %s: %s",
            user_uuid,
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

    def _resolve_user_uuid_from_session(self) -> Optional[str]:
        session_uuid = self.orchestration_event.orchestration_session_uuid
        if not session_uuid:
            return None

        try:
            response = _get_orchestrator_api_manager().call(
                "get_orchestration_session_user_data",
                orchestration_session_uuid=str(session_uuid),
                internal_orchestration_session_uuid=self.orchestration_event.internal_orchestration_session_uuid,
                access_token=self.orchestration_event.access_token,
                organization_id=str(self.orchestration_event.organization.organization_id),
                timeout=30,
            )
        except Exception as exc:
            logger.warning("Failed to resolve session user data: %s", exc)
            return None

        user_uuid = _extract_user_uuid(response)
        logger.info(
            "Resolved user_uuid from session user data: %s (response keys: %s)",
            user_uuid,
            sorted(response.keys()) if isinstance(response, dict) else type(response).__name__,
        )
        return user_uuid


def _extract_user_uuid(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None

    for key in ("target_user_uuid", "user_uuid", "chask_user_uuid"):
        value = payload.get(key)
        if value:
            return str(value)

    for key in ("user", "chask_user", "target_user", "member"):
        value = payload.get(key)
        if isinstance(value, dict):
            nested_uuid = _extract_user_uuid(value)
            if nested_uuid:
                return nested_uuid
            if value.get("uuid"):
                return str(value["uuid"])

    customer = payload.get("organization_customer")
    if isinstance(customer, dict):
        for key in ("user_uuid", "chask_user_uuid"):
            if customer.get(key):
                return str(customer[key])
        user = customer.get("user")
        if isinstance(user, dict):
            nested_uuid = _extract_user_uuid(user)
            if nested_uuid:
                return nested_uuid
        if isinstance(user, str) and user:
            return user

    active_users = payload.get("active_conversation_users")
    if isinstance(active_users, list):
        for item in active_users:
            if isinstance(item, dict):
                nested_uuid = _extract_user_uuid(item)
                if nested_uuid:
                    return nested_uuid

    return None
