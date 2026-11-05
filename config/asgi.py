"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

django_application = get_asgi_application()


async def application(scope, receive, send):
    if scope['type'] == 'websocket':
        from agents.terminal_transport import terminal_application
        await terminal_application(scope, receive, send)
    elif scope['type'] == 'lifespan':
        while True:
            event = await receive()
            if event['type'] == 'lifespan.startup':
                await send({'type': 'lifespan.startup.complete'})
            elif event['type'] == 'lifespan.shutdown':
                from agents.terminal_transport import HUB
                for link in list(HUB.links.values()):
                    link.end('server_shutdown')
                await send({'type': 'lifespan.shutdown.complete'})
                return
    else:
        await django_application(scope, receive, send)
