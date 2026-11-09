"""Native lifecycle compatibility API."""
from gateway.platforms import current
_backend = current()
SingleInstance = _backend.SingleInstance
ChildJob = _backend.ChildJob
decrypt_token = _backend.decrypt_token
