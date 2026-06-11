import hashlib
import hmac
import secrets

from django.conf import settings

from .models import InventoryAgent


def generate_inventory_agent_token() -> str:
    return f'access_inv_{secrets.token_urlsafe(32)}'


def hash_inventory_agent_token(token: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        token.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def create_inventory_agent_with_token(name: str, hostname: str, description: str = ''):
    token = generate_inventory_agent_token()
    agent = InventoryAgent.objects.create(
        name=name,
        hostname=hostname,
        description=description,
        token_hash=hash_inventory_agent_token(token),
    )
    return agent, token


def authenticate_inventory_agent_token(token: str):
    if not token:
        return None

    token_hash = hash_inventory_agent_token(token)
    agent = InventoryAgent.objects.filter(enabled=True, token_hash=token_hash).first()
    if agent and hmac.compare_digest(agent.token_hash, token_hash):
        return agent
    return None
