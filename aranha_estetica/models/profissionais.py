# aranha_estetica/models/profissionais.py — Profissionais, disponibilidade, bloqueios
from datetime import datetime, timedelta

from django.db import models


class Profissional(models.Model):
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
        """Slots livres no dia, considerando:
          - Feriado bloqueador
          - ExcecaoDisponibilidade (folga ou horario diferente da regra semanal)
          - DisponibilidadeProfissional (regra semanal)
          - Atendimentos confirmados/pendentes (com buffer do procedimento)
          - BloqueioAgenda
          - min_notice_horas / max_advance_dias do profissional

        procedimento opcional: aplica buffer_minutos ao avaliar conflitos.
        """
        from django.utils import timezone

        Feriado = self._get_model('Feriado')
        if Feriado.objects.filter(data=data_selecionada, bloqueia_agendamento=True).exists():
            return []

        agora = timezone.localtime()
        limite_min_notice = agora + timedelta(hours=self.min_notice_horas)
        limite_max_advance = (agora + timedelta(days=self.max_advance_dias)).date()
        if data_selecionada > limite_max_advance:
            return []

        excecoes = ExcecaoDisponibilidade.objects.filter(
            profissional=self, data=data_selecionada
        )
        excecao_horario = None
        for ex in excecoes:
            if ex.tipo == 'FOLGA':
                return []
            if ex.tipo == 'HORARIO_DIFERENTE' and ex.hora_inicio and ex.hora_fim:
                excecao_horario = ex
                break

        if excecao_horario:
            janelas = [(excecao_horario.hora_inicio, excecao_horario.hora_fim)]
        else:
            dia_semana = data_selecionada.isoweekday() % 7 + 1
            disponibilidades = DisponibilidadeProfissional.objects.filter(
                profissional=self,
                dia_semana=dia_semana
            )
            if not disponibilidades.exists():
                return []
            janelas = [(d.hora_inicio, d.hora_fim) for d in disponibilidades]

        agendamentos = list(self._get_model('Atendimento').objects.filter(
            profissional=self,
            data_hora_inicio__date=data_selecionada,
            status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
        ).select_related('procedimento'))

        from datetime import datetime as _dt
        dia_inicio = _dt.combine(data_selecionada, _dt.min.time())
        dia_fim = _dt.combine(data_selecionada, _dt.max.time())
        if timezone.is_naive(dia_inicio):
            tz = timezone.get_current_timezone()
            dia_inicio = timezone.make_aware(dia_inicio, tz)
            dia_fim = timezone.make_aware(dia_fim, tz)

        # Bloqueios pontuais (sem recorrencia) intersectando o dia
        bloqueios_raw = list(BloqueioAgenda.objects.filter(
            profissional=self,
        ).filter(
            models.Q(regra_recorrencia='') &
            models.Q(data_hora_inicio__date__lte=data_selecionada) &
            models.Q(data_hora_fim__date__gte=data_selecionada)
        ))
        # Bloqueios recorrentes (qualquer profissional self) — expande p/ janela do dia
        recorrentes = BloqueioAgenda.objects.filter(profissional=self).exclude(regra_recorrencia='')
        bloqueios_intervalos = [(b.data_hora_inicio, b.data_hora_fim) for b in bloqueios_raw]
        for b in recorrentes:
            bloqueios_intervalos.extend(b.expandir_ocorrencias(dia_inicio, dia_fim))

        buffer_proc = timedelta(minutes=procedimento.buffer_minutos) if procedimento else timedelta(0)

        horarios_disponiveis = []
        intervalo = timedelta(minutes=30)

        for hora_ini, hora_fim in janelas:
            hora_atual = datetime.combine(data_selecionada, hora_ini)
            hora_fim_expediente = datetime.combine(data_selecionada, hora_fim)
            if timezone.is_naive(hora_atual):
                tz = timezone.get_current_timezone()
                hora_atual = timezone.make_aware(hora_atual, tz)
                hora_fim_expediente = timezone.make_aware(hora_fim_expediente, tz)

            while hora_atual < hora_fim_expediente:
                if hora_atual < limite_min_notice:
                    hora_atual += intervalo
                    continue

                horario_ocupado = False
                for ag in agendamentos:
                    buf_ag = timedelta(minutes=ag.procedimento.buffer_minutos) if ag.procedimento_id else timedelta(0)
                    bloqueio_fim = ag.data_hora_fim + max(buf_ag, buffer_proc)
                    if ag.data_hora_inicio <= hora_atual < bloqueio_fim:
                        horario_ocupado = True
                        break
                if not horario_ocupado:
                    for bl_ini, bl_fim in bloqueios_intervalos:
                        if bl_ini <= hora_atual < bl_fim:
                            horario_ocupado = True
                            break
                if not horario_ocupado:
                    horario_str = hora_atual.strftime('%H:%M')
                    if horario_str not in horarios_disponiveis:
                        horarios_disponiveis.append(horario_str)
                hora_atual += intervalo

        return sorted(horarios_disponiveis)

    @staticmethod
    def _get_model(name):
        from django.apps import apps
        return apps.get_model('aranha_estetica', name)


class DisponibilidadeProfissional(models.Model):
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE)
    dia_semana = models.SmallIntegerField()  # 1=Dom, 2=Seg, ..., 7=Sab
    hora_inicio = models.TimeField()
    hora_fim = models.TimeField()

    class Meta:
        managed = True
        db_table = 'disponibilidade_profissional'
        constraints = [
            models.CheckConstraint(
                check=models.Q(dia_semana__gte=1) & models.Q(dia_semana__lte=7),
                name='chk_disponibilidade_dia_semana'
            ),
            models.CheckConstraint(
                check=models.Q(hora_fim__gt=models.F('hora_inicio')),
                name='chk_disponibilidade_hora_fim_apos_inicio',
            ),
            models.UniqueConstraint(
                fields=['profissional', 'dia_semana', 'hora_inicio'],
                name='uniq_disponibilidade_prof_dia_hora',
            ),
        ]


class BloqueioAgenda(models.Model):
    profissional = models.ForeignKey(
        Profissional, on_delete=models.CASCADE, blank=True, null=True
    )
    data_hora_inicio = models.DateTimeField()
    data_hora_fim = models.DateTimeField()
    motivo = models.TextField(blank=True, null=True)
    # iCal RRULE (RFC 5545). Vazio = bloqueio unico (sem recorrencia).
    # Ex: "FREQ=WEEKLY;BYDAY=WE" (toda quarta), "FREQ=MONTHLY;BYDAY=1SA" (1o sabado).
    regra_recorrencia = models.CharField(max_length=255, blank=True, default='')
    # Limite de expansao da regra. None = sem fim (mas engine usa janela).
    recorrencia_ate = models.DateField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'bloqueio_agenda'
        constraints = [
            models.CheckConstraint(
                check=models.Q(data_hora_fim__gt=models.F('data_hora_inicio')),
                name='chk_bloqueio_fim_maior_inicio'
            ),
        ]

    def __str__(self):
        prof = self.profissional.nome if self.profissional_id else 'Todos'
        ini = self.data_hora_inicio.strftime('%d/%m/%Y %H:%M') if self.data_hora_inicio else '?'
        sfx = f' (recorrente: {self.regra_recorrencia})' if self.regra_recorrencia else ''
        return f'Bloqueio {prof} @ {ini}{sfx}'

    def expandir_ocorrencias(self, range_inicio, range_fim):
        """Retorna lista de (inicio, fim) entre range_inicio e range_fim.

        Sem recorrencia: retorna a unica ocorrencia se intersectar.
        Com recorrencia: usa dateutil.rrule p/ expandir.
        """
        from datetime import timedelta as _td
        duracao = self.data_hora_fim - self.data_hora_inicio

        if not self.regra_recorrencia:
            if self.data_hora_fim > range_inicio and self.data_hora_inicio < range_fim:
                return [(self.data_hora_inicio, self.data_hora_fim)]
            return []

        try:
            from dateutil.rrule import rrulestr
        except ImportError:
            return [(self.data_hora_inicio, self.data_hora_fim)]

        try:
            rule = rrulestr(
                f'DTSTART:{self.data_hora_inicio.strftime("%Y%m%dT%H%M%SZ")}\n'
                f'RRULE:{self.regra_recorrencia}',
                forceset=True,
            )
        except (ValueError, TypeError):
            return [(self.data_hora_inicio, self.data_hora_fim)]

        ate = self.recorrencia_ate
        fim_busca = range_fim
        if ate:
            from datetime import datetime as _dt
            limite = _dt.combine(ate, _dt.min.time())
            from django.utils import timezone as _tz
            if _tz.is_naive(limite):
                limite = _tz.make_aware(limite, _tz.get_current_timezone())
            if limite < fim_busca:
                fim_busca = limite

        ocorrencias = []
        for inicio in rule.between(range_inicio - duracao, fim_busca, inc=True):
            fim = inicio + duracao
            if fim > range_inicio and inicio < range_fim:
                ocorrencias.append((inicio, fim))
        return ocorrencias


class ExcecaoDisponibilidade(models.Model):
    """Override pontual da DisponibilidadeProfissional em data especifica.
    FOLGA: bloqueia o dia inteiro (mesmo se houver regra semanal).
    HORARIO_DIFERENTE: substitui regra semanal pelo intervalo informado.
    """
    TIPO_CHOICES = [
        ('FOLGA', 'Folga'),
        ('HORARIO_DIFERENTE', 'Horario Diferente'),
    ]

    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE)
    data = models.DateField()
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    hora_inicio = models.TimeField(blank=True, null=True)
    hora_fim = models.TimeField(blank=True, null=True)
    motivo = models.TextField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'excecao_disponibilidade'
        indexes = [
            models.Index(fields=['profissional', 'data'], name='idx_excecao_prof_data'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['profissional', 'data', 'tipo'],
                name='uniq_excecao_prof_data_tipo',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(tipo='FOLGA') |
                    (
                        models.Q(tipo='HORARIO_DIFERENTE')
                        & models.Q(hora_inicio__isnull=False)
                        & models.Q(hora_fim__isnull=False)
                    )
                ),
                name='chk_excecao_horario_completo',
            ),
        ]

    def __str__(self):
        return f'{self.profissional.nome} {self.data} ({self.get_tipo_display()})'
