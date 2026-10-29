# aranha_estetica/models/clientes.py — Clientes
import secrets
from django.db import models, transaction
from django.core.validators import MinValueValidator

from django.db.models.functions import Now, Upper

from aranha_estetica.constants import MAX_FALTAS_ANTES_BLOQUEIO
from aranha_estetica.validators import (
    validate_cpf, validate_telefone_br, validate_data_nascimento,
    normalizar_telefone, normalizar_cpf,
)

from ..db.indices import Indice
from .mixins import ATUALIZADO_EM_BACKFILL


class ClienteAtivosManager(models.Manager):
    """Default manager que exclui clientes soft-deleted."""

    def get_queryset(self):
        return super().get_queryset().filter(deletado_em__isnull=True)


class Cliente(models.Model):
    # COLLATE pt_br (ICU no PG; shim em db/collation.py no SQLite) — no estado desde a 0054
    nome = models.CharField(
        max_length=150, db_collation='pt_br', db_comment='COLLATE pt_br (ICU) - ordenacao correta de acentos.',
    )
    data_nascimento = models.DateField(
        blank=True, null=True, validators=[validate_data_nascimento],
    )
    # unique global removido — uq_cliente_cpf_ativo (parcial) permite
    # re-cadastro pos soft-delete; max 11 (digits-only canonico); ausente = NULL
    cpf = models.CharField(
        max_length=11, blank=True, null=True, validators=[validate_cpf],
    )
    # lookups usam email__iexact (UPPER(email)) -> o UNIQUE parcial uq_cliente_email_ativo
    # (UPPER) serve de indice e garante unicidade entre ativos; ausente = NULL (nunca '')
    email = models.EmailField(max_length=254, blank=True, null=True)
    telefone = models.CharField(
        max_length=20, blank=True, null=True, validators=[validate_telefone_br],
    )
    cep = models.CharField(max_length=10, blank=True, null=True)
    endereco = models.CharField(max_length=255, blank=True, null=True)
    ativo = models.BooleanField(default=True, db_default=True, db_column='eh_ativo')
    faltas_consecutivas = models.SmallIntegerField(
        default=0, db_default=0, validators=[MinValueValidator(0)], db_column='qtd_faltas_consecutivas',
        db_comment='Zera ao REALIZADO; ao atingir MAX_FALTAS_BLOQUEIO liga eh_bloqueado_online.',
    )
    bloqueado_online = models.BooleanField(default=False, db_default=False, db_column='eh_bloqueado_online')

    # Fidelidade (programa de indicacao)
    indicado_por = models.ForeignKey(
        'self', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='indicacoes',
        help_text='Cliente que indicou este (programa fidelidade)',
        db_column='cliente_indicador_id', db_index=False,
        db_comment='Quem indicou este cliente (cashback de indicacao em movimento_carteira).',
    )
    # UNIQUE no Meta (uq_cliente_codigo_indicacao): sem o _like do unique=True
    codigo_indicacao = models.CharField(
        max_length=12, blank=True, null=True,
        help_text='Codigo curto compartilhavel (ex: A1B2C3D4) gerado automaticamente.',
        db_comment='Codigo curto compartilhavel (hex maiusculo) gerado no save.',
    )

    # LGPD: consentimentos de comunicacao (marketing por e-mail, pesquisa e
    # lembrete por WhatsApp) vivem em Consentimento (historico append-only,
    # migration 0050) — ler/gravar por services/consentimentos. O link de
    # descadastro e emitido por e-mail de marketing (link_acesso DESCADASTRO).

    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())
    atualizado_em = models.DateTimeField(auto_now=True, db_default=Now(), db_comment=ATUALIZADO_EM_BACKFILL)
    deletado_em = models.DateTimeField(blank=True, null=True)

    objects = ClienteAtivosManager()
    # all_objects: Manager nu de proposito — retorna TODOS (inclui soft-deleted),
    # usado por _gerar_codigo_indicacao/registrar_falta. ATENCAO:
    # all_objects.filter(...).delete() e hard delete (ignora Cliente.delete()).
    all_objects = models.Manager()

    class Meta:
        managed = True
        db_table = 'cliente'
        db_table_comment = (
            'Cliente da clinica. Telefone (so digitos, 10-11) e a chave natural do agendamento publico '
            '(uq_cliente_telefone_ativo). Soft delete em deletado_em; anonimizacao LGPD troca nome por '
            '[ANONIMIZADO-<id>] (sp_anonimizar_cliente). Consentimentos em consentimento.'
        )
        indexes = [
            Indice(fields=['nome'], name='ix_cliente_nome'),
            Indice(
                fields=['indicado_por'], condition=models.Q(indicado_por__isnull=False),
                name='ix_cliente_cliente_indicador_parcial',
            ),
            # purga LGPD (deletado_em < limite); IS NULL (quase todos) nao e seletivo
            Indice(
                fields=['deletado_em'], condition=models.Q(deletado_em__isnull=False),
                name='ix_cliente_deletado_em_parcial',
            ),
        ]
        constraints = [
            models.UniqueConstraint(fields=['codigo_indicacao'], name='uq_cliente_codigo_indicacao'),
            # CHAVE NATURAL do booking publico: 1 cliente ativo por telefone (ausente = NULL)
            models.UniqueConstraint(
                fields=['telefone'], condition=models.Q(deletado_em__isnull=True), name='uq_cliente_telefone_ativo',
            ),
            # CPF unico entre ativos
            models.UniqueConstraint(
                fields=['cpf'], condition=models.Q(deletado_em__isnull=True), name='uq_cliente_cpf_ativo',
            ),
            # e-mail unico entre ativos, sem caixa (maria@X.com == MARIA@x.com); e o indice do
            # email__iexact (UPPER(email::text) = UPPER(%s))
            models.UniqueConstraint(
                Upper('email'), condition=models.Q(deletado_em__isnull=True), name='uq_cliente_email_ativo',
            ),
            models.CheckConstraint(
                condition=~models.Q(indicado_por_id=models.F('id')),
                name='ck_cliente_nao_indica_a_si',
            ),
            models.CheckConstraint(
                condition=models.Q(faltas_consecutivas__gte=0), name='ck_cliente_qtd_faltas_nao_negativa',
            ),
            models.CheckConstraint(
                condition=models.Q(email__isnull=True) | ~models.Q(email=''), name='ck_cliente_email_nao_vazio',
            ),
            # PG (0055, regex): ck_cliente_telefone_formato / ck_cliente_cpf_formato
        ]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        # Forma canonica de identidade (remodelagem v2.1 fase 3) — telefone e
        # cpf sempre digits-only; lookups das views comparam normalizado.
        # Tamanho NAO e validado aqui (ValidationError no save vira 500): a
        # borda valida com validators.validate_telefone_br/validate_cpf (forms/cliente.py).
        # Ausente = NULL, nunca '' (0054: ck_cliente_email_nao_vazio e os CHECK de
        # formato do PG nao aceitam vazio; os UNIQUE parciais so valem p/ NOT NULL).
        self.telefone = (normalizar_telefone(self.telefone) or None) if self.telefone else None
        self.cpf = (normalizar_cpf(self.cpf) or None) if self.cpf else None
        self.email = self.email or None
        if not self.codigo_indicacao:
            self.codigo_indicacao = self._gerar_codigo_indicacao()
        super().save(*args, **kwargs)

    @staticmethod
    def _gerar_codigo_indicacao() -> str:
        """Gera codigo unico 8 chars hex maiusculo. Tenta 8 vezes — alfabeto 16^8 = 4G colisao improvavel."""
        for _ in range(8):
            candidato = secrets.token_hex(4).upper()
            if not Cliente.all_objects.filter(codigo_indicacao=candidato).exists():
                return candidato
        # fallback: 12 chars caso 8 colisoes (loteria)
        return secrets.token_hex(6).upper()

    @staticmethod
    def limite_faltas_bloqueio() -> int:
        """Faltas consecutivas p/ bloquear o agendamento online.

        Configuracao 'MAX_FALTAS_BLOQUEIO' (painel) > MAX_FALTAS_ANTES_BLOQUEIO.
        """
        from .configuracao import Configuracao
        valor = (
            Configuracao.objects.filter(chave='MAX_FALTAS_BLOQUEIO')
            .values_list('valor', flat=True).first()
        )
        try:
            limite = int(str(valor).strip())
        except (TypeError, ValueError):
            return MAX_FALTAS_ANTES_BLOQUEIO
        return limite if limite >= 1 else MAX_FALTAS_ANTES_BLOQUEIO

    def registrar_falta(self):
        # Lock da row p/ evitar lost-update sob no-shows concorrentes; grava
        # so os campos afetados (evita reprocessar normalizacao/tokens no save()).
        limite = self.limite_faltas_bloqueio()
        with transaction.atomic():
            travado = Cliente.all_objects.select_for_update().get(pk=self.pk)
            travado.faltas_consecutivas += 1
            if travado.faltas_consecutivas >= limite:
                travado.bloqueado_online = True
            travado.save(update_fields=['faltas_consecutivas', 'bloqueado_online', 'atualizado_em'])
        self.faltas_consecutivas = travado.faltas_consecutivas
        self.bloqueado_online = travado.bloqueado_online

    def resetar_faltas(self):
        self.faltas_consecutivas = 0
        self.bloqueado_online = False
        self.save(update_fields=['faltas_consecutivas', 'bloqueado_online', 'atualizado_em'])

    def soft_delete(self):
        from django.utils import timezone
        self.deletado_em = timezone.now()
        self.ativo = False
        self.save(update_fields=['deletado_em', 'ativo', 'atualizado_em'])

    def delete(self, using=None, keep_parents=False, hard=False):
        """Soft-delete por padrao (consistencia com soft_delete + manager de ativos).

        Evita hard-delete acidental de Cliente (que cascatearia / esbarraria em
        FKs PROTECT). Para apagar de verdade (ex.: erasure LGPD), use hard=True.
        """
        if hard:
            return super().delete(using=using, keep_parents=keep_parents)
        self.soft_delete()
