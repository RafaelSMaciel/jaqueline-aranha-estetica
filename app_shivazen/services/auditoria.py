"""Servico de auditoria — wrapper do LogAuditoria com captura de IP e masking de PII."""
import logging
from typing import Any

from app_shivazen.models import LogAuditoria
from app_shivazen.utils.security import client_ip, mask_email, mask_cpf, mask_telefone

logger = logging.getLogger(__name__)


class AuditoriaService:
    SENSITIVE_KEYS = {'email', 'cpf', 'telefone', 'senha', 'password'}

    @classmethod
    def registrar(
        cls,
        *,
        request=None,
        usuario=None,
        acao: str,
        tabela_afetada: str = '',
        id_registro: int | None = None,
        detalhes: dict[str, Any] | None = None,
    ) -> LogAuditoria:
        ip = client_ip(request) if request is not None else None
        detalhes_sanitizados = cls._sanitize(detalhes) if detalhes else {}

        # Resolve usuario: param explicito > request.user autenticado > None (anonimo).
        if usuario is None and request is not None:
            req_user = getattr(request, 'user', None)
            if req_user is not None and getattr(req_user, 'is_authenticated', False):
                usuario = req_user

        return LogAuditoria.objects.create(
            usuario=usuario,
            acao=acao,
            tabela_afetada=tabela_afetada,
            id_registro_afetado=id_registro,
            detalhes=detalhes_sanitizados,
            ip_origem=ip or None,
        )

    @classmethod
    def _sanitize(cls, detalhes: dict) -> dict:
        """Aplica masking em chaves sensiveis (PII) antes de persistir."""
        out = {}
        for k, v in detalhes.items():
            kl = k.lower()
            if kl in cls.SENSITIVE_KEYS and isinstance(v, str):
                if kl == 'email':
                    out[k] = mask_email(v)
                elif kl == 'cpf':
                    out[k] = mask_cpf(v)
                elif kl == 'telefone':
                    out[k] = mask_telefone(v)
                else:
                    out[k] = '***'
            else:
                out[k] = v
        return out
