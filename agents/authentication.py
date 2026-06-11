from rest_framework import exceptions

from .models import AgentMachine, hash_agent_token


AUTHORIZATION_PREFIX = 'Bearer'


def authenticate_agent_token(request) -> AgentMachine:
    authorization = request.headers.get('Authorization', '')
    parts = authorization.split()

    if len(parts) != 2 or parts[0] != AUTHORIZATION_PREFIX:
        raise exceptions.AuthenticationFailed('Invalid or missing bearer token.')

    token_hash = hash_agent_token(parts[1])

    try:
        machine = AgentMachine.objects.get(agent_token_hash=token_hash)
    except AgentMachine.DoesNotExist as exc:
        raise exceptions.AuthenticationFailed('Invalid bearer token.') from exc

    if not machine.is_active:
        raise exceptions.AuthenticationFailed('Agent is inactive.')

    return machine
