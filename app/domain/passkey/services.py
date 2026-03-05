from dataclasses import dataclass

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url, base64url_to_bytes
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core.config import settings
from app.core.errors import BadRequestError, NotFoundError
from app.domain.passkey.repositories import PasskeyRepository


@dataclass
class PasskeyChallengeData:
    challenge: str
    user_id: int | None = None


class PasskeyService:
    def __init__(self, repo: PasskeyRepository) -> None:
        self._repo = repo
        self._rp_id = getattr(settings, "webauthn_rp_id", "localhost")
        self._rp_name = getattr(settings, "webauthn_rp_name", "Cheese Community")
        self._origin = getattr(settings, "webauthn_origin", "http://localhost:5173")

    async def generate_registration_options(
        self,
        *,
        user_id: int,
        username: str,
        display_name: str | None = None,
    ) -> dict:
        existing_creds = await self._repo.list_by_user(user_id)
        exclude_credentials = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(cred.credential_id))
            for cred in existing_creds
        ]

        options = generate_registration_options(
            rp_id=self._rp_id,
            rp_name=self._rp_name,
            user_id=str(user_id).encode(),
            user_name=username,
            user_display_name=display_name or username,
            exclude_credentials=exclude_credentials,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
        )

        return {
            "challenge": bytes_to_base64url(options.challenge),
            "rp": {"id": options.rp.id, "name": options.rp.name},
            "user": {
                "id": bytes_to_base64url(options.user.id),
                "name": options.user.name,
                "displayName": options.user.display_name,
            },
            "pubKeyCredParams": [
                {"type": p.type, "alg": p.alg} for p in options.pub_key_cred_params
            ],
            "timeout": options.timeout,
            "excludeCredentials": [
                {"id": bytes_to_base64url(c.id), "type": c.type}
                for c in (options.exclude_credentials or [])
            ],
            "authenticatorSelection": {
                "residentKey": options.authenticator_selection.resident_key.value
                if options.authenticator_selection
                else None,
                "userVerification": options.authenticator_selection.user_verification.value
                if options.authenticator_selection
                else None,
            },
        }

    async def verify_registration(
        self,
        *,
        user_id: int,
        challenge: str,
        credential: dict,
    ) -> dict:
        try:
            verification = verify_registration_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(challenge),
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
            )
        except Exception as e:
            raise BadRequestError(f"Passkey verification failed: {e}")

        transports = credential.get("response", {}).get("transports", [])
        device_type = getattr(verification, "credential_device_type", "single_device")
        backed_up = getattr(verification, "credential_backed_up", False)

        cred = await self._repo.create(
            user_id=user_id,
            credential_id=bytes_to_base64url(verification.credential_id),
            public_key=verification.credential_public_key,
            counter=verification.sign_count,
            device_type=device_type,
            backed_up=backed_up,
            transports=transports,
        )

        return {
            "id": cred.id,
            "credentialId": cred.credential_id,
            "deviceType": cred.device_type,
            "backedUp": cred.backed_up,
            "createdAt": int(cred.created_at.timestamp() * 1000),
        }

    async def generate_authentication_options(
        self,
        user_id: int | None = None,
    ) -> dict:
        allow_credentials = []
        if user_id is not None:
            creds = await self._repo.list_by_user(user_id)
            allow_credentials = [
                PublicKeyCredentialDescriptor(
                    id=base64url_to_bytes(cred.credential_id),
                    transports=cred.transports.split(",") if cred.transports else None,
                )
                for cred in creds
            ]

        options = generate_authentication_options(
            rp_id=self._rp_id,
            allow_credentials=allow_credentials if allow_credentials else None,
            user_verification=UserVerificationRequirement.PREFERRED,
        )

        return {
            "challenge": bytes_to_base64url(options.challenge),
            "rpId": options.rp_id,
            "timeout": options.timeout,
            "allowCredentials": [
                {
                    "id": bytes_to_base64url(c.id),
                    "type": c.type,
                    "transports": c.transports,
                }
                for c in (options.allow_credentials or [])
            ],
            "userVerification": options.user_verification.value,
        }

    async def verify_authentication(
        self,
        *,
        challenge: str,
        credential: dict,
    ) -> int:
        credential_id_b64 = credential.get("id") or credential.get("rawId")
        if not credential_id_b64:
            raise BadRequestError("Missing credential ID")

        stored_cred = await self._repo.get_by_credential_id(credential_id_b64)
        if stored_cred is None:
            raise NotFoundError("Passkey not found")

        try:
            verification = verify_authentication_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(challenge),
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                credential_public_key=stored_cred.public_key,
                credential_current_sign_count=stored_cred.counter,
            )
        except Exception as e:
            raise BadRequestError(f"Passkey authentication failed: {e}")

        await self._repo.update_counter(
            credential_id=credential_id_b64,
            counter=verification.new_sign_count,
        )

        return stored_cred.user_id

    async def list_passkeys(self, user_id: int) -> list[dict]:
        creds = await self._repo.list_by_user(user_id)
        return [
            {
                "id": cred.id,
                "credentialId": cred.credential_id,
                "deviceType": cred.device_type,
                "backedUp": cred.backed_up,
                "createdAt": int(cred.created_at.timestamp() * 1000),
                "updatedAt": int(cred.updated_at.timestamp() * 1000),
            }
            for cred in creds
        ]

    async def delete_passkey(self, user_id: int, credential_id: str) -> bool:
        deleted = await self._repo.delete_by_credential_id(credential_id, user_id)
        if deleted:
            return True
        try:
            cred_int = int(credential_id)
            return await self._repo.delete_by_id(cred_int, user_id)
        except ValueError:
            return False
