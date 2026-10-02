# aranha_estetica/models/profissionais.py — Profissionais
from django.db import models


class Profissional(models.Model):
    # PG: COLLATE pt_br aplicada pela 0038 fora do estado do Django — um
    # AlterField futuro em `nome` reseta a collation (reaplicar via RunSQL).
    nome = models.CharField(max_length=100)
    slug = models.SlugField(max_length=140, unique=True, blank=True, null=True)
    especialidade = models.TextField(blank=True, null=True)
    foto_url = models.URLField(max_length=600, blank=True, default='')
    bio = models.TextField(blank=True, default='', help_text='Biografia publica do profissional')
    ativo = models.BooleanField(default=True)
    min_notice_horas = models.SmallIntegerField(default=2)
    max_advance_dias = models.SmallIntegerField(default=60)
    ics_token = models.CharField(max_length=64, unique=True, blank=True, null=True)
    # Google Calendar sync — preenchido apos OAuth dance
    gcal_refresh_token = models.TextField(blank=True, default='')
    gcal_calendar_id = models.CharField(max_length=200, blank=True, default='primary')
    gcal_sync_token = models.TextField(blank=True, default='')
    gcal_ultimo_sync_em = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'profissional'
        indexes = [
            models.Index(fields=['ativo'], name='idx_profissional_ativo'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(min_notice_horas__gte=0) & models.Q(min_notice_horas__lte=720),
                name='chk_profissional_min_notice_range',
            ),
            models.CheckConstraint(
                check=models.Q(max_advance_dias__gte=1) & models.Q(max_advance_dias__lte=365),
                name='chk_profissional_max_advance_range',
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
        if not self.ics_token:
            import secrets
            self.ics_token = secrets.token_urlsafe(32)
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
