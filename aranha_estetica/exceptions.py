"""Hierarquia de excecoes do dominio.

Convencao:
    DomainError              — regra de negocio violada (HTTP 400/409/422)
        BusinessRuleViolation — invariante de negocio quebrado (409)
        ResourceNotFound      — recurso de dominio nao existe (404)
        AuthorizationError    — acao negada por permissao (403)
        ValidationError       — input mal-formatado (400)

    IntegrationError         — dependencia externa falhou (HTTP 502/503)
        TransientError        — passivel de retry (503)
        PermanentError        — falha definitiva (502)

Uso em views:
    try:
        AgendamentoService().criar(cmd)
    except SlotIndisponivelError as e:
        return JsonResponse({'erro': e.codigo, 'msg': str(e)}, status=409)
    except DomainError as e:
        return JsonResponse({'erro': str(e)}, status=400)
"""


class DomainError(Exception):
    """Base — regra de negocio violada."""
    codigo: str = 'domain_error'
    http_status: int = 400


class BusinessRuleViolation(DomainError):
    """Invariante de negocio quebrado (ex: slot ocupado, cliente bloqueado)."""
    codigo = 'business_rule_violation'
    http_status = 409


class ResourceNotFound(DomainError):
    """Recurso de dominio nao existe (preferir sobre Http404 em services)."""
    codigo = 'resource_not_found'
    http_status = 404


class AuthorizationError(DomainError):
    """Acao bloqueada por permissao insuficiente."""
    codigo = 'forbidden'
    http_status = 403


class ValidationError(DomainError):
    """Input nao passou em regra de validacao de dominio."""
    codigo = 'validation_error'
    http_status = 400


# ─── Excecoes especificas de agendamento ──────────────────────────────
class SlotIndisponivelError(BusinessRuleViolation):
    codigo = 'slot_indisponivel'

    def __init__(self, motivo: str = 'Horario indisponivel'):
        super().__init__(motivo)
        self.motivo = motivo


class ClienteBloqueadoError(BusinessRuleViolation):
    codigo = 'cliente_bloqueado'


class TransicaoStatusInvalida(BusinessRuleViolation):
    codigo = 'transicao_invalida'


class OtpInvalidoError(ValidationError):
    codigo = 'otp_invalido'


class OtpExpiradoError(ValidationError):
    codigo = 'otp_expirado'


# ─── Integracoes externas ─────────────────────────────────────────────
class IntegrationError(Exception):
    """Base — falha em servico externo (Zenvia, WhatsApp, GCal, SMTP)."""
    codigo = 'integration_error'
    http_status = 502


class TransientError(IntegrationError):
    """Passivel de retry (timeout, 5xx temporario)."""
    codigo = 'transient_error'
    http_status = 503


class PermanentError(IntegrationError):
    """Falha definitiva (4xx, credenciais invalidas)."""
    codigo = 'permanent_error'
    http_status = 502
