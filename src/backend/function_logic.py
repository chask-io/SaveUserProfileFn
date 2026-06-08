import logging
import os
from typing import Any, Dict

from chask_foundation.api.api_manager import ApiManager
from chask_foundation.backend.models import OrchestrationEvent

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _api_base_url() -> str:
    base_domain = os.getenv("BASE_DOMAIN")
    if not base_domain:
        raise ValueError("BASE_DOMAIN is required for chask_api calls")
    if base_domain.startswith("http://") or base_domain.startswith("https://"):
        return f"{base_domain.rstrip('/')}/api/v2/users"
    return f"https://{base_domain.rstrip('/')}/api/v2/users"


users_api_manager = ApiManager(base_url=_api_base_url())


@users_api_manager.register(
    "save_user_profile",
    "save-user-profile",
    "POST",
)
def save_user_profile(**payload: Any) -> Dict[str, Any]:
    return {"json": payload}


class FunctionBackend:
    def __init__(self, orchestration_event: OrchestrationEvent):
        self.orchestration_event = orchestration_event
        logger.info(
            "Initialized SaveUserProfileFn for org: %s",
            orchestration_event.organization.organization_id,
        )

    def process_request(self) -> str:
        extra_params = self.orchestration_event.extra_params or {}
        user_uuid = extra_params.get("target_user_uuid")
        if not user_uuid:
            return "No target user was found in this conversation context, so the profile was not saved."

        orchestration_session_uuid = self.orchestration_event.orchestration_session_uuid
        if not orchestration_session_uuid:
            return "No orchestration session was found in this event, so the profile was not saved."

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
        users_api_manager.call(
            "save_user_profile",
            access_token=self.orchestration_event.access_token,
            organization_id=str(self.orchestration_event.organization.organization_id),
            timeout=30,
            **payload,
        )

        return "Saved the user's profile details."

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
            key: value
            for key, value in ((field, tool_args.get(field)) for field in allowed_fields)
            if value not in (None, "", [], {})
        }
