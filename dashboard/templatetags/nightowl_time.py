from django import template
from django.utils import timezone


register = template.Library()


@register.filter
def relative_time(value):
    if not value:
        return '-'

    delta = timezone.now() - value
    seconds = max(int(delta.total_seconds()), 0)

    if seconds < 60:
        return 'agora'

    minutes = seconds // 60
    if minutes < 60:
        return f'ha {minutes} min'

    hours = minutes // 60
    if hours < 24:
        return f'ha {hours} h'

    days = hours // 24
    return f'ha {days} d'


@register.filter
def time_until(value):
    if not value:
        return '-'

    delta = value - timezone.now()
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return 'expirado'

    minutes = seconds // 60
    if minutes < 60:
        return f'em {minutes} min'

    hours = minutes // 60
    if hours < 24:
        return f'em {hours} h'

    days = hours // 24
    return f'em {days} d'
