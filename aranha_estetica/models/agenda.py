# aranha_estetica/models/agenda.py — Agenda do profissional: grade semanal + excecoes
"""Agenda em 2 tabelas (docs/specs/padrao-sql.md §2.4 e §4.a — migration 0048).

AgendaHorario  grade semanal de expediente: varias janelas por dia, sem
               sobreposicao (ex_agenda_horario_sobreposicao, so-PG).
AgendaExcecao  tudo que muda a grade numa data:
  FOLGA              dia(s) inteiro(s) [data_inicio..data_fim]. profissional NULL =
                     clinica inteira: feriado (origem FERIADO_NACIONAL/LOCAL),
                     recesso (RECESSO) ou fechamento manual.
  HORARIO_DIFERENTE  substitui a grade num dia (so de profissional).
  BLOQUEIO           intervalo exato [data_hora_inicio, data_hora_fim), pontual ou
                     recorrente (RRULE no subconjunto de utils.recorrencia; DTSTART =
                     data_hora_inicio em hora local; data_fim_recorrencia inclusiva).
  ativo=False (coluna eh_ativo) = registrada mas ignorada (ex.: feriado nacional
  em que a clinica abre — sem a linha o carregar_feriados o recriaria).

Unifica as 4 tabelas antigas da agenda (grade 1=Dom..7=Sab, excecao de 1 dia,
bloqueio e feriado — migration 0048). Nomes de banco no padrao (§1); a regra de
slots mora so no SlotService (services/disponibilidade.py).
"""
from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Now
from django.utils import timezone

from ..db.indices import Indice
from ..utils.datas import fmt_local
from ..utils.recorrencia import RegraNaoSuportada, normalizar_regra_recorrencia


def _aware(dt):
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


class AgendaHorario(models.Model):
    """Janela de expediente num dia da semana (ISO: 1=segunda ... 7=domingo)."""

    DIA_SEMANA_CHOICES = [
        (1, 'Segunda-feira'),
        (2, 'Terça-feira'),
        (3, 'Quarta-feira'),
        (4, 'Quinta-feira'),
        (5, 'Sexta-feira'),
        (6, 'Sábado'),
        (7, 'Domingo'),
    ]

    # sem indice de FK: o gist do ex_agenda_horario_sobreposicao (PG) cobre
    # profissional_id = X (spec §2.4)
    profissional = models.ForeignKey(
        'Profissional', on_delete=models.CASCADE, related_name='agenda_horarios', db_index=False,
    )
    dia_semana = models.SmallIntegerField(
        choices=DIA_SEMANA_CHOICES,
        db_comment='ISO 8601: 1=segunda ... 7=domingo (= extract(isodow) e date.isoweekday()).',
    )
    hora_inicio = models.TimeField()
    hora_fim = models.TimeField()
    criado_em = models.DateTimeField(default=timezone.now, db_default=Now(), editable=False)
    atualizado_em = models.DateTimeField(auto_now=True, db_default=Now())

    class Meta:
        db_table = 'agenda_horario'
        db_table_comment = (
            'Grade semanal de expediente do profissional (varias janelas por dia, sem sobreposicao).'
        )
        constraints = [
            models.CheckConstraint(
                condition=Q(dia_semana__range=(1, 7)), name='ck_agenda_horario_dia_semana',
            ),
            models.CheckConstraint(
                condition=Q(hora_fim__gt=F('hora_inicio')), name='ck_agenda_horario_fim_apos_inicio',
            ),
        ]

    def __str__(self):
        return (f'{self.profissional} {self.get_dia_semana_display()} '
                f'{self.hora_inicio:%H:%M}-{self.hora_fim:%H:%M}')

    def clean(self):
        super().clean()
        if self.hora_inicio and self.hora_fim and self.hora_fim <= self.hora_inicio:
            raise ValidationError({'hora_fim': 'A hora de fim deve ser depois da hora de início.'})
        if self.profissional_id and self.dia_semana and self.hora_inicio and self.hora_fim:
            # espelho do ex_agenda_horario_sobreposicao (so existe no PG)
            sobrepostas = AgendaHorario.objects.filter(
                profissional_id=self.profissional_id, dia_semana=self.dia_semana,
                hora_inicio__lt=self.hora_fim, hora_fim__gt=self.hora_inicio,
            ).exclude(pk=self.pk)
            if sobrepostas.exists():
                raise ValidationError('Esta janela se sobrepõe a outra do mesmo dia.')


class AgendaExcecaoQuerySet(models.QuerySet):
    def ativas(self):
        return self.filter(ativo=True)

    def do_profissional_ou_global(self, profissional):
        """Excecoes do profissional + as da clinica inteira (profissional NULL)."""
        pk = getattr(profissional, 'pk', profissional)
        return self.filter(Q(profissional_id=pk) | Q(profissional__isnull=True))

    def cobrindo(self, dia):
        """FOLGA/HORARIO_DIFERENTE cujo intervalo de datas contem `dia`."""
        return self.filter(
            tipo__in=(AgendaExcecao.TIPO_FOLGA, AgendaExcecao.TIPO_HORARIO_DIFERENTE),
            data_inicio__lte=dia, data_fim__gte=dia,
        )

    def folga_global_em(self, dia):
        """Feriado/recesso/fechamento da clinica ativo em `dia` (use .exists())."""
        return self.ativas().filter(
            tipo=AgendaExcecao.TIPO_FOLGA, profissional__isnull=True,
        ).cobrindo(dia)

    def vigente_para(self, profissional, tipo, data_inicio, data_fim=None):
        """A excecao ativa que o painel atualiza ao reenviar o mesmo periodo (upsert): HORARIO_DIFERENTE
        pelo dia; FOLGA pelo mesmo intervalo [data_inicio, data_fim]. None se nao ha."""
        qs = self.ativas().filter(profissional=profissional, tipo=tipo, data_inicio=data_inicio)
        if tipo != AgendaExcecao.TIPO_HORARIO_DIFERENTE:
            qs = qs.filter(data_fim=data_fim or data_inicio)
        return qs.first()

    def bloqueios_no_intervalo(self, inicio, fim):
        """BLOQUEIO que pode ter ocorrencia em [inicio, fim): pontual que
        intersecta ou recorrente iniciado antes do fim (expandir com
        .ocorrencias)."""
        return self.filter(tipo=AgendaExcecao.TIPO_BLOQUEIO, data_hora_inicio__lt=fim).filter(
            Q(regra_recorrencia__isnull=False) | Q(data_hora_fim__gt=inicio),
        )


class AgendaExcecao(models.Model):
    TIPO_FOLGA = 'FOLGA'
    TIPO_HORARIO_DIFERENTE = 'HORARIO_DIFERENTE'
    TIPO_BLOQUEIO = 'BLOQUEIO'
    TIPO_CHOICES = [
        (TIPO_FOLGA, 'Folga'),
        (TIPO_HORARIO_DIFERENTE, 'Horário diferente'),
        (TIPO_BLOQUEIO, 'Bloqueio'),
    ]
    TIPOS_POR_DATA = (TIPO_FOLGA, TIPO_HORARIO_DIFERENTE)

    ORIGEM_MANUAL = 'MANUAL'
    ORIGEM_FERIADO_NACIONAL = 'FERIADO_NACIONAL'
    ORIGEM_FERIADO_LOCAL = 'FERIADO_LOCAL'
    ORIGEM_RECESSO = 'RECESSO'
    ORIGEM_CHOICES = [
        (ORIGEM_MANUAL, 'Manual (painel)'),
        (ORIGEM_FERIADO_NACIONAL, 'Feriado nacional'),
        (ORIGEM_FERIADO_LOCAL, 'Feriado local (estadual/municipal)'),
        (ORIGEM_RECESSO, 'Recesso da clínica'),
    ]
    ORIGENS_GLOBAIS = (ORIGEM_FERIADO_NACIONAL, ORIGEM_FERIADO_LOCAL, ORIGEM_RECESSO)

    profissional = models.ForeignKey(
        'Profissional', on_delete=models.CASCADE, null=True, blank=True,
        related_name='agenda_excecoes', db_index=False,
        help_text='Vazio = clínica inteira (feriado, recesso, bloqueio global).',
    )
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    origem = models.CharField(
        max_length=20, choices=ORIGEM_CHOICES, default=ORIGEM_MANUAL, db_default=ORIGEM_MANUAL,
        db_comment='MANUAL (painel) | FERIADO_NACIONAL/FERIADO_LOCAL (carregar_feriados) | '
                   'RECESSO (clinica).',
    )
    data_inicio = models.DateField(
        null=True, blank=True,
        db_comment='FOLGA/HORARIO_DIFERENTE: primeiro dia (data local). NULL em BLOQUEIO.',
    )
    data_fim = models.DateField(
        null=True, blank=True,
        db_comment='FOLGA: ultimo dia (inclusivo). HORARIO_DIFERENTE: = data_inicio.',
    )
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fim = models.TimeField(null=True, blank=True)
    data_hora_inicio = models.DateTimeField(null=True, blank=True)
    data_hora_fim = models.DateTimeField(null=True, blank=True)
    regra_recorrencia = models.CharField(
        max_length=255, null=True, blank=True,
        help_text='Só BLOQUEIO. Ex.: FREQ=WEEKLY;BYDAY=WE (toda quarta), FREQ=MONTHLY;BYDAY=1SA '
                  '(1º sábado), FREQ=DAILY;INTERVAL=2. Vazio = bloqueio único.',
        db_comment='RRULE (RFC 5545) no subconjunto suportado (fn_ocorrencias_recorrencia): FREQ '
                   'DAILY/WEEKLY/MONTHLY, INTERVAL, BYDAY (ordinal so em MONTHLY) ou BYMONTHDAY. '
                   'DTSTART = data_hora_inicio (hora local). Sem COUNT/UNTIL: use data_fim_recorrencia.',
    )
    data_fim_recorrencia = models.DateField(
        null=True, blank=True,
        help_text='Último dia (inclusivo) em que uma ocorrência pode começar. Vazio = sem fim.',
        db_comment='Ultimo dia (inclusivo) em que uma ocorrencia pode comecar.',
    )
    motivo = models.TextField(blank=True, default='', db_default='')
    observacao = models.TextField(blank=True, default='', db_default='')
    ativo = models.BooleanField(default=True, db_default=True, db_column='eh_ativo')
    usuario_autor = models.ForeignKey(
        'Usuario', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        db_index=False, editable=False,
    )
    criado_em = models.DateTimeField(default=timezone.now, db_default=Now(), editable=False)
    atualizado_em = models.DateTimeField(auto_now=True, db_default=Now())

    objects = AgendaExcecaoQuerySet.as_manager()

    class Meta:
        db_table = 'agenda_excecao'
        db_table_comment = (
            'Excecoes da grade semanal: FOLGA (dia(s) inteiro(s)), HORARIO_DIFERENTE (substitui a '
            'grade num dia) e BLOQUEIO (intervalo exato, pontual ou recorrente). profissional_id '
            'NULL = clinica inteira (feriado, recesso, bloqueio global). eh_ativo=false = '
            'registrada mas ignorada (ex.: feriado nacional em que a clinica abre).'
        )
        constraints = [
            models.CheckConstraint(
                condition=Q(tipo__in=['FOLGA', 'HORARIO_DIFERENTE', 'BLOQUEIO']),
                name='ck_agenda_excecao_tipo',
            ),
            models.CheckConstraint(
                condition=Q(origem__in=['MANUAL', 'FERIADO_NACIONAL', 'FERIADO_LOCAL', 'RECESSO']),
                name='ck_agenda_excecao_origem',
            ),
            models.CheckConstraint(
                condition=~Q(tipo='FOLGA') | Q(
                    data_inicio__isnull=False, data_fim__isnull=False, data_fim__gte=F('data_inicio'),
                    hora_inicio__isnull=True, hora_fim__isnull=True,
                    data_hora_inicio__isnull=True, data_hora_fim__isnull=True,
                    regra_recorrencia__isnull=True, data_fim_recorrencia__isnull=True,
                ),
                name='ck_agenda_excecao_folga',
            ),
            models.CheckConstraint(
                condition=~Q(tipo='HORARIO_DIFERENTE') | Q(
                    profissional__isnull=False,
                    data_inicio__isnull=False, data_fim__isnull=False, data_fim=F('data_inicio'),
                    hora_inicio__isnull=False, hora_fim__isnull=False, hora_fim__gt=F('hora_inicio'),
                    data_hora_inicio__isnull=True, data_hora_fim__isnull=True,
                    regra_recorrencia__isnull=True, data_fim_recorrencia__isnull=True,
                ),
                name='ck_agenda_excecao_horario_diferente',
            ),
            models.CheckConstraint(
                condition=~Q(tipo='BLOQUEIO') | Q(
                    data_hora_inicio__isnull=False, data_hora_fim__isnull=False,
                    data_hora_fim__gt=F('data_hora_inicio'),
                    data_inicio__isnull=True, data_fim__isnull=True,
                    hora_inicio__isnull=True, hora_fim__isnull=True,
                ),
                name='ck_agenda_excecao_bloqueio',
            ),
            models.CheckConstraint(
                condition=Q(regra_recorrencia__isnull=False) | Q(data_fim_recorrencia__isnull=True),
                name='ck_agenda_excecao_recorrencia',
            ),
            models.CheckConstraint(
                condition=~Q(origem__in=['FERIADO_NACIONAL', 'FERIADO_LOCAL', 'RECESSO'])
                | Q(profissional__isnull=True, tipo='FOLGA'),
                name='ck_agenda_excecao_feriado_global',
            ),
            models.UniqueConstraint(
                fields=['profissional', 'data_inicio'],
                condition=Q(tipo='HORARIO_DIFERENTE', ativo=True),
                name='uq_agenda_excecao_horario_diferente_dia',
            ),
            models.UniqueConstraint(
                fields=['origem', 'data_inicio'],
                condition=Q(origem__in=['FERIADO_NACIONAL', 'FERIADO_LOCAL']),
                name='uq_agenda_excecao_feriado',
            ),
        ]
        indexes = [
            Indice(fields=['profissional', 'data_fim'], name='ix_agenda_excecao_profissional_data_fim'),
            Indice(
                fields=['profissional', 'data_hora_fim'], name='ix_agenda_excecao_profissional_data_hora_fim',
            ),
            Indice(
                fields=['usuario_autor'], condition=Q(usuario_autor__isnull=False),
                name='ix_agenda_excecao_usuario_autor_parcial',
            ),
        ]

    def __str__(self):
        alvo = self.profissional.nome if self.profissional_id else 'Todos'
        if self.tipo == self.TIPO_BLOQUEIO:
            ini = fmt_local(self.data_hora_inicio) or '?'
            sfx = f' (recorrente: {self.regra_recorrencia})' if self.regra_recorrencia else ''
            return f'Bloqueio {alvo} @ {ini}{sfx}'
        periodo = self.periodo_display
        if self.tipo == self.TIPO_HORARIO_DIFERENTE and self.hora_inicio and self.hora_fim:
            periodo += f' {self.hora_inicio:%H:%M}-{self.hora_fim:%H:%M}'
        rotulo = self.motivo or self.get_tipo_display()
        return f'{alvo} {periodo} ({rotulo})'

    @property
    def periodo_display(self):
        if not self.data_inicio:
            return '?'
        if self.data_fim and self.data_fim != self.data_inicio:
            return f'{self.data_inicio:%d/%m/%Y} a {self.data_fim:%d/%m/%Y}'
        return f'{self.data_inicio:%d/%m/%Y}'

    def clean(self):
        """Espelho dos CHECKs (mensagem legivel) + RRULE normalizada. Uma regra por metodo; os erros
        de todas se juntam num ValidationError so (campo -> mensagem)."""
        super().clean()
        erros: dict[str, str] = {}
        self._normalizar_vazios()
        if self.tipo in self.TIPOS_POR_DATA:
            self._validar_por_data(erros)
        elif self.tipo == self.TIPO_BLOQUEIO:
            self._validar_bloqueio(erros)
        self._validar_origem(erros)
        self._validar_folga_sobreposta(erros)
        if erros:
            raise ValidationError(erros)

    def _normalizar_vazios(self) -> None:
        """RRULE vazia = sem recorrencia; HORARIO_DIFERENTE vale um dia; FOLGA sem fim = um dia."""
        if self.regra_recorrencia is not None and not self.regra_recorrencia.strip():
            self.regra_recorrencia = None
        if self.tipo == self.TIPO_HORARIO_DIFERENTE:
            self.data_fim = self.data_inicio
        elif self.tipo == self.TIPO_FOLGA and self.data_inicio and not self.data_fim:
            self.data_fim = self.data_inicio

    def _validar_por_data(self, erros: dict) -> None:
        """FOLGA e HORARIO_DIFERENTE: datas, sem data e hora nem recorrencia (ck_agenda_excecao_folga e
        ck_agenda_excecao_horario_diferente)."""
        if not self.data_inicio:
            erros['data_inicio'] = 'Informe a data.'
        elif self.data_fim < self.data_inicio:
            erros['data_fim'] = 'A data final deve ser igual ou posterior à inicial.'
        if self.data_hora_inicio or self.data_hora_fim:
            erros['data_hora_inicio'] = 'Folga/horário diferente usam datas, não data e hora.'
        if self.regra_recorrencia or self.data_fim_recorrencia:
            erros['regra_recorrencia'] = 'Recorrência só vale para BLOQUEIO.'
        if self.tipo == self.TIPO_FOLGA and (self.hora_inicio or self.hora_fim):
            erros['hora_inicio'] = 'Folga ocupa o dia inteiro (sem horas).'
        if self.tipo == self.TIPO_HORARIO_DIFERENTE:
            if not self.profissional_id:
                erros['profissional'] = 'Horário diferente é sempre de um profissional.'
            if not self.hora_inicio or not self.hora_fim:
                erros['hora_inicio'] = 'Informe a hora de início e a de fim.'
            elif self.hora_fim <= self.hora_inicio:
                erros['hora_fim'] = 'A hora de fim deve ser depois da hora de início.'

    def _validar_bloqueio(self, erros: dict) -> None:
        """BLOQUEIO: intervalo exato (ck_agenda_excecao_bloqueio) e a RRULE no subconjunto suportado
        (normalizada; o limite da regra vira data_fim_recorrencia)."""
        if not self.data_hora_inicio or not self.data_hora_fim:
            erros['data_hora_inicio'] = 'Informe início e fim do bloqueio.'
        elif self.data_hora_fim <= self.data_hora_inicio:
            erros['data_hora_fim'] = 'O fim deve ser posterior ao início.'
        if self.data_inicio or self.data_fim or self.hora_inicio or self.hora_fim:
            erros['data_inicio'] = 'Bloqueio usa data e hora de início/fim (não datas soltas).'
        if not self.regra_recorrencia or 'data_hora_inicio' in erros:
            return
        try:
            regra, limite = normalizar_regra_recorrencia(self.regra_recorrencia, dtstart=self.data_hora_inicio)
        except RegraNaoSuportada as exc:
            erros['regra_recorrencia'] = f'Recorrência não suportada: {exc}.'
            return
        self.regra_recorrencia = regra
        if limite and (not self.data_fim_recorrencia or limite < self.data_fim_recorrencia):
            self.data_fim_recorrencia = limite

    def _validar_origem(self, erros: dict) -> None:
        """Fim de recorrencia sem regra (ck_agenda_excecao_recorrencia) e feriado/recesso so como FOLGA
        da clinica inteira (ck_agenda_excecao_feriado_global)."""
        if self.data_fim_recorrencia and not self.regra_recorrencia:
            erros['data_fim_recorrencia'] = 'Fim da recorrência sem regra de recorrência.'
        if self.origem in self.ORIGENS_GLOBAIS and (self.profissional_id or self.tipo != self.TIPO_FOLGA):
            erros['origem'] = 'Feriado/recesso é FOLGA da clínica inteira (sem profissional).'

    def _validar_folga_sobreposta(self, erros: dict) -> None:
        """Espelho do ex_agenda_excecao_folga_sobreposta (so existe no PG): duas folgas ativas do mesmo
        profissional nao se cruzam."""
        if not (self.tipo == self.TIPO_FOLGA and self.ativo and self.profissional_id
                and self.data_inicio and self.data_fim and 'data_fim' not in erros):
            return
        if AgendaExcecao.objects.ativas().filter(
            profissional_id=self.profissional_id, tipo=self.TIPO_FOLGA,
            data_inicio__lte=self.data_fim, data_fim__gte=self.data_inicio,
        ).exclude(pk=self.pk).exists():
            erros['data_inicio'] = 'Já existe folga deste profissional nesse período.'

    def ocorrencias(self, range_inicio, range_fim):
        """Intervalos (inicio, fim) aware desta excecao que intersectam [range_inicio, range_fim).

        BLOQUEIO: pontual = o proprio intervalo; recorrente = expansao da RRULE
        (dateutil) com DTSTART no fuso LOCAL — BYDAY/BYMONTHDAY sao avaliados no
        dia local (em UTC, um bloqueio de quarta 21h BRT cairia na quinta) — e
        data_fim_recorrencia INCLUSIVA.
        FOLGA: o(s) dia(s) inteiro(s) no fuso local. HORARIO_DIFERENTE: [] (e
        janela de trabalho, nao bloqueio).
        """
        if self.tipo == self.TIPO_FOLGA:
            ini = _aware(datetime.combine(self.data_inicio, time.min))
            fim = _aware(datetime.combine(self.data_fim + timedelta(days=1), time.min))
            return [(ini, fim)] if fim > range_inicio and ini < range_fim else []
        if self.tipo != self.TIPO_BLOQUEIO:
            return []

        duracao = self.data_hora_fim - self.data_hora_inicio
        if not self.regra_recorrencia:
            if self.data_hora_fim > range_inicio and self.data_hora_inicio < range_fim:
                return [(self.data_hora_inicio, self.data_hora_fim)]
            return []

        from dateutil.rrule import rrulestr

        inicio_local = self.data_hora_inicio
        if timezone.is_aware(inicio_local):
            inicio_local = timezone.localtime(inicio_local)
        try:
            rule = rrulestr(f'RRULE:{self.regra_recorrencia}', dtstart=inicio_local, forceset=True)
        except (ValueError, TypeError):
            # defensivo: clean()/CHECK do PG so deixam gravar o subconjunto
            return [(self.data_hora_inicio, self.data_hora_fim)]

        fim_busca = range_fim
        if self.data_fim_recorrencia:
            limite = _aware(datetime.combine(self.data_fim_recorrencia + timedelta(days=1), time.min))
            if limite < fim_busca:
                fim_busca = limite

        ocorrencias = []
        for inicio in rule.between(range_inicio - duracao, fim_busca, inc=True):
            if fim_busca <= inicio:
                continue
            fim = inicio + duracao
            if fim > range_inicio and inicio < range_fim:
                ocorrencias.append((inicio, fim))
        return ocorrencias
