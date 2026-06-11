import os
from dataclasses import dataclass, field


@dataclass
class UserContext:
    user_id: int
    prpt_username: str
    prpt_password: str
    prpt_rep_id: str
    ghl_api_token: str
    ghl_location_id: str
    enable_ghl_push: bool = True

    # ── Path helpers ───────────────────────────────────────────────────────

    @property
    def data_dir(self) -> str:
        return os.path.join("data", str(self.user_id))

    @property
    def session_file(self) -> str:
        return os.path.join("sessions", f"auth_{self.user_id}.json")

    def path(self, filename: str) -> str:
        return os.path.join(self.data_dir, filename)

    def ensure_dirs(self):
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "backups"), exist_ok=True)
        os.makedirs("sessions", exist_ok=True)


def build_context(user: dict) -> UserContext:
    """Build a UserContext from a DB row dict."""
    return UserContext(
        user_id=user["id"],
        prpt_username=user.get("prpt_username") or "",
        prpt_password=user.get("prpt_password") or "",
        prpt_rep_id=user.get("prpt_rep_id") or "",
        ghl_api_token=user.get("ghl_api_token") or "",
        ghl_location_id=user.get("ghl_location_id") or "",
        enable_ghl_push=True,
    )
