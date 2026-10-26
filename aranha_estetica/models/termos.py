# aranha_estetica/models/termos.py — Termos de consentimento
import hashlib

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Now

from ..db.campos import HashSha256Field
from ..db.indices import Indice
from .clientes import Cliente
from .mixins import atualizado_em_legado, criado_em_legado
from .procedimentos import Procedimento

# Consentimento especifico p/ dado de saude (LGPD art. 11, I): texto da
# VersaoTermo SAUDE v1.0 (migration 0047) — wizard do booking e ficha publica
# mostram o texto da versao vigente e gravam o aceite dela (AceiteTermo).
TERMO_SAUDE_VERSAO = '1.0'
TERMO_SAUDE_TITULO = 'Consentimento para uso de dados de saúde (LGPD, art. 11)'
TEXTO_CONSENTIMENTO_SAUDE = (
    'Autorizo a clínica a usar as informações de saúde que informei neste '
    'questionário (como alergias, gestação e medicamentos) somente para avaliar '
    'a segurança do procedimento e cuidar do meu atendimento, conforme a '
    'Política de Privacidade (LGPD, art. 11).'
)


class VersaoTermo(models.Model):
    """Template versionado de um termo (LGPD, dados de saude ou procedimento).

    Imutavel apos o 1o aceite: o texto aceito e a prova (LGPD art. 8 §1).
    Mudou o texto -> publicar nova versao (a anterior e arquivada). Desativar
    (ativa=False) continua permitido.
    """
    TIPO_CHOICES = [
        ('LGPD', 'LGPD / Privacidade'),
        ('SAUDE', 'Dados de saúde (LGPD art. 11)'),
        ('PROCEDIMENTO', 'Termo de Procedimento'),
    ]
    # Campos que compoem a prova do aceite — congelados quando ha aceites
    CAMPOS_IMUTAVEIS = ('tipo', 'procedimento', 'titulo', 'conteudo', 'versao')

    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    procedimento = models.ForeignKey(
        Procedimento, on_delete=models.CASCADE, blank=True, null=True, db_index=False,
    )
    titulo = models.TextField()
    conteudo = models.TextField()
    versao = models.CharField(max_length=20)
    vigente_desde = models.DateField(db_column='data_inicio_vigencia')
    ativa = models.BooleanField(default=True, db_default=True, db_column='eh_ativo')
    criado_em = criado_em_legado()
    atualizado_em = atualizado_em_legado()

    class Meta:
        managed = True
        db_table = 'versao_termo'
        db_table_comment = (
            'Texto versionado de termo (LGPD, SAUDE art. 11, PROCEDIMENTO). Imutavel depois do 1o aceite '
            '(tg_versao_termo_imutavel); 1 ativo por escopo.'
        )
        constraints = [
            models.CheckConstraint(
                condition=models.Q(tipo__in=['LGPD', 'SAUDE', 'PROCEDIMENTO']),
                name='ck_versao_termo_tipo'
            ),
            # so 1 versao ativa por escopo (tipo+procedimento; LGPD/SAUDE = procedimento NULL)
            models.UniqueConstraint(
                fields=['tipo', 'procedimento'],
                condition=models.Q(ativa=True),
                name='uq_versao_termo_ativa_por_procedimento',
            ),
            models.UniqueConstraint(
                fields=['tipo'],
                condition=models.Q(ativa=True) & models.Q(procedimento__isnull=True),
                name='uq_versao_termo_ativa_global',
            ),
        ]
        indexes = [
            Indice(
                fields=['procedimento'], condition=models.Q(procedimento__isnull=False),
                name='ix_versao_termo_procedimento_parcial',
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

    @classmethod
    def saude_vigente(cls):
        """Versao do consentimento de dados de saude (art. 11) ativa ou None."""
        return (
            cls.objects.filter(tipo='SAUDE', procedimento__isnull=True, ativa=True)
            .order_by('-vigente_desde', '-pk')
            .first()
        )

    @classmethod
    def texto_saude_vigente(cls):
        """(versao SAUDE vigente|None, texto exibido ao cliente).

        O texto mostrado e o da versao que sera gravada no aceite (o SHA-256
        prova o que foi lido); sem versao ativa, o texto padrao.
        """
        termo = cls.saude_vigente()
        return termo, (termo.conteudo if termo is not None else TEXTO_CONSENTIMENTO_SAUDE)

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
    """Aceite/assinatura de termo pelo cliente — LGPD, dados de saude ou procedimento.

    Unifica AceitePrivacidade + AssinaturaTermoProcedimento (remodelagem
    v2.1 fase 6): a distincao vive em versao_termo.tipo. atendimento = o
    atendimento em cujo fluxo o aceite foi dado (booking / link do termo).
    on_delete=PROTECT no cliente: aceite e evidencia legal (LGPD art. 8) —
    anonimizar, nunca cascatear. Gravar sempre por AceiteTermo.registrar().
    """
    # FKs sem indice automatico: cliente coberto pelo UNIQUE (prefixo), os outros pelos ix_
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='aceites', db_index=False)
    versao_termo = models.ForeignKey(VersaoTermo, on_delete=models.RESTRICT, db_index=False)
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.SET_NULL, blank=True, null=True, db_index=False,
    )
    ip = models.GenericIPAddressField(blank=True, null=True, db_column='ip_origem')
    user_agent = models.CharField(max_length=500, blank=True, default='', db_default='')
    # SHA-256 do texto aceito (char(64)); NULL = aceite anterior a 0044 (sem prova do texto)
    conteudo_sha256 = HashSha256Field(
        blank=True, null=True, db_column='conteudo_hash',
        db_comment='SHA-256 do versao_termo.conteudo aceito; NULL = aceite anterior a 0044 (sem prova do texto).',
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())

    class Meta:
        managed = True
        db_table = 'aceite_termo'
        db_table_comment = (
            'Prova do aceite (LGPD art. 8): IP, user-agent e SHA-256 do texto lido. Imutavel '
            '(tg_aceite_termo_imutavel); retencao 20 anos.'
        )
        constraints = [
            models.UniqueConstraint(
                fields=['cliente', 'versao_termo'],
                name='uq_aceite_termo_cliente_versao',
            ),
            # PG (0055, regex): ck_aceite_termo_conteudo_hash_formato
        ]
        indexes = [
            Indice(fields=['versao_termo'], name='ix_aceite_termo_versao_termo'),
            Indice(
                fields=['atendimento'], condition=models.Q(atendimento__isnull=False),
                name='ix_aceite_termo_atendimento_parcial',
            ),
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
