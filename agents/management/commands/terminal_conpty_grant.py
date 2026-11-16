import json
import os

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from agents.terminal_grants import (
    MAX_GRANT_MINUTES, PILOT_ENDPOINT_ID, PILOT_MACHINE_ID, PILOT_OPERATOR_USERNAME,
    change_conpty_grant, inspect_conpty_grant, verify_execution_identity,
    validate_grant_context,
)


class Command(BaseCommand):
    help = 'Consulta ou administra somente o grant ConPTY do piloto explicitamente fixado.'

    def add_arguments(self, parser):
        parser.add_argument('--action', choices=('status', 'grant', 'revoke'), required=True)
        parser.add_argument('--endpoint-id', required=True)
        parser.add_argument('--machine-id', required=True)
        parser.add_argument('--operator', required=True)
        parser.add_argument('--reason', default='')
        parser.add_argument('--approval-ref', default='')
        parser.add_argument('--ttl-minutes', type=int)
        parser.add_argument('--apply', action='store_true', help='Aplica a alteração; sem isto, grant/revoke são apenas prévias.')
        parser.add_argument('--confirm-endpoint', default='', help='Repita o UUID piloto para confirmar uma alteração real.')

    def handle(self, *args, **options):
        try:
            actor = verify_execution_identity(
                effective_uid=os.geteuid() if hasattr(os, 'geteuid') else -1,
                sudo_user=os.environ.get('SUDO_USER', ''),
                sudo_uid=os.environ.get('SUDO_UID', ''),
                allowed_users=getattr(settings, 'NIGHTOWL_TERMINAL_GRANT_ADMIN_USERS', set()),
            )
            self._validate_pilot_args(options)
            action = options['action']
            if action == 'status':
                result = inspect_conpty_grant(endpoint_id=options['endpoint_id'],
                    machine_id=options['machine_id'], operator_username=options['operator'])
            else:
                if not options['reason'] or not options['approval_ref']:
                    raise ValidationError('Grant/revoke exige --reason e --approval-ref.')
                reason, approval_ref = validate_grant_context(options['reason'], options['approval_ref'])
                if action == 'grant' and options['ttl_minutes'] is None:
                    raise ValidationError('--ttl-minutes é obrigatório para grant.')
                if action == 'revoke' and options['ttl_minutes'] is not None:
                    raise ValidationError('--ttl-minutes não se aplica à revogação.')
                if not options['apply']:
                    current = inspect_conpty_grant(endpoint_id=options['endpoint_id'],
                        machine_id=options['machine_id'], operator_username=options['operator'])
                    if current['allow_admin_auth']:
                        raise PermissionDenied('O grant contém allow_admin_auth=true; revisão manual obrigatória.')
                    if action == 'revoke' and not current['grant_exists']:
                        raise ValidationError('Não existe grant para revogar; nenhuma alteração seria feita.')
                    result = {'preview': True, 'action': action, 'target': current,
                        'ttl_minutes': options['ttl_minutes'], 'reason': reason,
                        'approval_ref': approval_ref, 'no_changes': True}
                else:
                    if options['confirm_endpoint'] != str(PILOT_ENDPOINT_ID):
                        raise ValidationError('--apply exige --confirm-endpoint com o UUID completo do piloto.')
                    result = change_conpty_grant(action=action,
                        endpoint_id=options['endpoint_id'], machine_id=options['machine_id'],
                        operator_username=options['operator'], actor=actor,
                        reason=reason, approval_ref=approval_ref,
                        ttl_minutes=options['ttl_minutes'])
            self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
        except (PermissionDenied, ValidationError, ValueError) as exc:
            detail = '; '.join(getattr(exc, 'messages', [str(exc)]))
            raise CommandError(detail) from None
        except DatabaseError:
            raise CommandError('Falha ou contenção no banco; a transação foi revertida. Nenhuma alteração foi confirmada.') from None

    @staticmethod
    def _validate_pilot_args(options):
        if options['endpoint_id'] != str(PILOT_ENDPOINT_ID):
            raise ValidationError('Informe o UUID completo autorizado do piloto.')
        if options['machine_id'].casefold() != PILOT_MACHINE_ID:
            raise ValidationError('Informe o machine_id esperado do piloto.')
        if options['operator'].casefold() != PILOT_OPERATOR_USERNAME:
            raise ValidationError('Beneficiário fora do escopo fixo do piloto.')
        if options['ttl_minutes'] is not None and not 1 <= options['ttl_minutes'] <= MAX_GRANT_MINUTES:
            raise ValidationError(f'--ttl-minutes deve estar entre 1 e {MAX_GRANT_MINUTES}.')
