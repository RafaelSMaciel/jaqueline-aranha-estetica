# aranha_estetica/models/profissionais.py — Profissionais
from django.db import models

from .mixins import atualizado_em_legado, criado_em_legado


class Profissional(models.Model):
    # COLLATE pt_br (ICU no PG; shim em db/collation.py no SQLite) — no estado desde a 0054
    nome = models.CharField(max_length=100, db_collation='pt_br')
    # UNIQUE no Meta (uq_profissional_slug): sem o indice/_like do SlugField
    slug = models.SlugField(max_length=140, blank=True, null=True, db_index=False)
    especialidade = models.TextField(blank=True, default='', db_default='')
    foto_url = models.URLField(max_length=600, blank=True, default='', db_default='', db_column='url_foto')
    bio = models.TextField(
        blank=True, default='', db_default='', help_text='Biografia publica do profissional', db_column='biografia',
    )
    ativo = models.BooleanField(default=True, db_default=True, db_column='eh_ativo')
    min_notice_horas = models.SmallIntegerField(
        default=2, db_default=2, db_column='antecedencia_minima_horas',
        db_comment='Horas minimas entre agora e o inicio de um horario oferecido online.',
    )
    max_advance_dias = models.SmallIntegerField(
        default=60, db_default=60, db_column='antecedencia_maxima_dias',
        db_comment='Dias maximos a frente oferecidos online.',
    )
    # Feed ICS: link_acesso FEED_ICS (gerado no painel; o token so aparece na geracao)
    # Google Calendar sync — preenchido apos OAuth dance
    gcal_refresh_token = models.TextField(
        blank=True, default='', db_default='', db_column='gcal_token_renovacao',
        db_comment=(
            'Refresh token OAuth do Google Calendar - segredo reversivel (nao da p/ hashear); '
            'ver padrao-sql secao 7.3 (cifrar).'
        ),
    )
    gcal_calendar_id = models.CharField(
        max_length=200, blank=True, default='primary', db_default='primary', db_column='gcal_calendario_ref',
        db_comment='Id do calendario no Google (externo).',
    )
    gcal_sync_token = models.TextField(blank=True, default='', db_default='', db_column='gcal_token_sincronizacao')
    gcal_ultimo_sync_em = models.DateTimeField(blank=True, null=True, db_column='gcal_sincronizado_em')
    criado_em = criado_em_legado()
    atualizado_em = atualizado_em_legado()

    class Meta:
        managed = True
        db_table = 'profissional'
        db_table_comment = (
            'Quem atende. Grade semanal em agenda_horario, folgas/bloqueios em agenda_excecao. '
            'Feed ICS por link_acesso (finalidade FEED_ICS).'
        )
        constraints = [
            models.UniqueConstraint(fields=['slug'], name='uq_profissional_slug'),
            models.CheckConstraint(
                condition=models.Q(min_notice_horas__gte=0) & models.Q(min_notice_horas__lte=720),
                name='ck_profissional_antecedencia_minima',
            ),
            models.CheckConstraint(
                condition=models.Q(max_advance_dias__gte=1) & models.Q(max_advance_dias__lte=365),
                name='ck_profissional_antecedencia_maxima',
            ),
        ]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            base = slugify(self.nome) or 'profissional'
            slug = base
            counter = 1
            while Profissional.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                counter += 1
                slug = f'{base}-{counter}'
            self.slug = slug
        super().save(*args, **kwargs)

    def get_horarios_disponiveis(self, data_selecionada, procedimento=None):
        """Slots livres no dia (feriado/folga, horario diferente, grade semanal,
        atendimentos com buffer, bloqueios, min_notice/max_advance). Logica em
        services.disponibilidade.SlotService — extraida do model para uma camada
        testavel; comportamento preservado (tests/test_slots_disponibilidade.py)."""
        from ..services.disponibilidade import SlotService
        return SlotService.slots_livres(self, data_selecionada, procedimento)


# Grade semanal, folgas, bloqueios e feriados: models/agenda.py (AgendaHorario +
# AgendaExcecao; as 4 tabelas antigas da agenda foram unificadas na migration 0048).
