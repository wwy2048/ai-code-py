import hashlib
import hmac
import re
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import Settings

_LEGACY_MD5_PATTERN = re.compile(r'^[0-9a-f]{32}$')
_LEGACY_SALT = 'hhh'


@dataclass(frozen=True)
class PasswordVerification:
    valid: bool
    needs_upgrade: bool


class PasswordService:
    """验证旧 MD5 密码并新写入 Argon2id 哈希。"""

    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            memory_cost=settings.password_hash_memory_cost,
            time_cost=settings.password_hash_time_cost,
            parallelism=settings.password_hash_parallelism,
        )

    def hash(self, raw_password: str) -> str:
        return self._hasher.hash(raw_password)

    def verify(self, stored_password: str, raw_password: str) -> PasswordVerification:
        if _LEGACY_MD5_PATTERN.fullmatch(stored_password):
            expected = hashlib.md5(f'{_LEGACY_SALT}{raw_password}'.encode()).hexdigest()
            return PasswordVerification(
                valid=hmac.compare_digest(expected, stored_password),
                needs_upgrade=True,
            )

        try:
            self._hasher.verify(stored_password, raw_password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return PasswordVerification(valid=False, needs_upgrade=False)
        needs_upgrade = self._hasher.check_needs_rehash(stored_password)
        return PasswordVerification(valid=True, needs_upgrade=needs_upgrade)
