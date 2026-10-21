"""A machine's owner-login row (`DeviceClaudeLoginRow`) as its owner reads it:
on 「我的设备」, 「这台设备」 and 「模型连接」 (#2991)."""

from app.domain.device.models import DeviceClaudeLoginRow


def view(row: DeviceClaudeLoginRow) -> dict:
    """What the machine last said about the login. Never a key: the machine
    does not send one."""
    return {
        "id": row.id,
        "local_id": row.local_id,
        "installed": row.installed,
        "logged_in": row.logged_in,
        "auth_method": row.auth_method,
        "subscription_type": row.subscription_type,
        # Which Claude account: never a token, only what names it.
        "account_email": row.account_email,
        "account_org": row.account_org,
        # What its owner named it.
        "name": row.name,
        "checked_at": row.checked_at.isoformat(),
        # Removed while the machine was away: out of use on the platform
        # already, logged out on the machine once it connects.
        "revoking": row.revoked_at is not None,
    }
