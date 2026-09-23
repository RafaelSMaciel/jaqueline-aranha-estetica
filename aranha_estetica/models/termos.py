# aranha_estetica/models/termos.py — Termos de consentimento
import hashlib

from django.core.exceptions import ValidationError
from django.db import models

from .clientes import Cliente
from .procedimentos import Procedimento


class VersaoTermo(models.Model):
    """Template versionado de um termo (LGPD ou por tipo de procedimento).

    Imutavel apos o 1o aceite: o texto aceito e a prova (LGPD art. 8 §1).
    Mudou o texto -> publicar nova versao (a anterior e arquivada). Desativar
    (ativa=False) continua permitido.
    """
    TIPO_CHOICES = [
        ('LGPD', 'LGPD / Privacidade'),
        ('PROCEDIMENTO', 'Termo de Procedimento'),
    ]
    # Campos que compoem a prova do aceite — congelados quando ha aceites
    CAMPOS_IMUTAVEIS = ('tipo', 'procedimento', 'titulo', 'conteudo', 'versao')

    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    procedimento = models.ForeignKey(
        Procedimento, on_delete=models.CASCADE, blank=True, null=True
    )
    titulo = models.TextField()
    conteudo = models.TextField()
    versao = models.CharField(max_length=20)
    vigente_desde = models.DateField()
    ativa = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'versao_termo'
        constraints = [
            models.CheckConstraint(
                check=models.Q(tipo__in=['LGPD', 'PROCEDIMENTO']),
                name='chk_versao_termo_tipo'
            ),
            # so 1 versao ativa por escopo (tipo+procedimento; LGPD = procedimento NULL)
            models.UniqueConstraint(
                fields=['tipo', 'procedimento'],
                condition=models.Q(ativa=True),
                name='uniq_termo_ativo_por_escopo',
            ),
            models.UniqueConstraint(
                fields=['tipo'],
                condition=models.Q(ativa=True) & models.Q(procedimento__isnull=True),
                name='uniq_termo_ativo_global',
            ),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} v{self.versao}'

    @classmethod
    def lgpd_vigente(cls):
        """Versao LGPD/Privacidade ativa (escopo global) ou None."""
        return (
            cls.objects.filter(tipo='LGPD', procedimento__isnull=True, ativa=True)
            .order_by('-vigente_desde', '-pk')
            .first()
        )

    @property
    def sha256_conteudo(self):
        """SHA-256 (hex) do texto do termo — gravado no aceite como prova."""
        return hashlib.sha256((self.conteudo or '').encode('utf-8')).hexdigest()

    def _validar_imutavel(self):
        if not self.pk:
            return
        attnames = [self._meta.get_field(c).attname for c in self.CAMPOS_IMUTAVEIS]
        original = type(self).objects.filter(pk=self.pk).values(*attnames).first()
        if original is None:
            return
        alterados = [a for a in attnames if original[a] != getattr(self, a)]
        if alterados and AceiteTermo.objects.filter(versao_termo_id=self.pk).exists():
            raise ValidationError(
                'Esta versão do termo já foi aceita por clientes e não pode ser editada. '
                'Publique uma nova versão.'
            )

    def clean(self):
        super().clean()
        self._validar_imutavel()

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if update_fields is None or set(update_fields) & set(self.CAMPOS_IMUTAVEIS):
            self._validar_imutavel()
        super().save(*args, **kwargs)


class AceiteTermo(models.Model):
    """Aceite/assinatura de termo pelo cliente — LGPD ou procedimento.

    Unifica AceitePrivacidade + AssinaturaTermoProcedimento (remodelagem
    v2.1 fase 6): a distincao vive em versao_termo.tipo. atendimento = o
    atendimento em cujo fluxo o aceite foi dado (booking / link do termo).
    on_delete=PROTECT no cliente: aceite e evidencia legal (LGPD art. 8) —
    anonimizar, nunca cascatear. Gravar sempre por AceiteTermo.registrar().
    """
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='aceites')
    versao_termo = models.ForeignKey(VersaoTermo, on_delete=models.RESTRICT)
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.SET_NULL, blank=True, null=True,
    )
    ip = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=500, blank=True, default='')
    # SHA-256 do texto aceito; vazio = aceite anterior a 0044 (sem prova do texto)
    conteudo_sha256 = models.CharField(max_length=64, blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'aceite_termo'
        constraints = [
            models.UniqueConstraint(
                fields=['cliente', 'versao_termo'],
                name='uniq_aceite_cliente_versao',
            ),
        ]
        indexes = [
            models.Index(fields=['cliente', '-criado_em'], name='idx_aceite_cli_data'),
        ]

    def __str__(self):
        return f'Aceite {self.versao_termo} por cliente {self.cliente_id}'

    @classmethod
    def registrar(cls, cliente, versao_termo, request=None, atendimento=None):
        """Grava o aceite com a prova (IP, user-agent, SHA-256 do texto).

        Idempotente por cliente+versao: se ja existe, devolve o existente sem
        alterar (a prova e a do 1o aceite). versao_termo=None -> None (nao ha
        termo vigente p/ registrar).
        """
        if versao_termo is None:
            return None
        ip, user_agent = None, ''
        if request is not None:
            from ..utils.security import IP_FALLBACK, client_ip
            ip = client_ip(request)
            if ip == IP_FALLBACK:  # sem IP real: NULL, nao um IP inventado
                ip = None
            user_agent = (request.META.get('HTTP_USER_AGENT') or '')[:500]
        aceite, _criado = cls.objects.get_or_create(
            cliente=cliente,
            versao_termo=versao_termo,
            defaults={
                'atendimento': atendimento,
                'ip': ip,
                'user_agent': user_agent,
                'conteudo_sha256': versao_termo.sha256_conteudo,
            },
        )
        return aceite
