# app_shivazen/models/profissionais.py — Profissionais, disponibilidade, bloqueios
from datetime import datetime, timedelta

from django.db import models


class Profissional(models.Model):
    nome = models.CharField(max_length=100)
    especialidade = models.TextField(blank=True, null=True)
    ativo = models.BooleanField(default=True)
    min_notice_horas = models.SmallIntegerField(default=2)
    max_advance_dias = models.SmallIntegerField(default=60)

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

        bloqueios = list(BloqueioAgenda.objects.filter(
            profissional=self,
            data_hora_inicio__date__lte=data_selecionada,
            data_hora_fim__date__gte=data_selecionada
        ))

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
                    for bl in bloqueios:
                        if bl.data_hora_inicio <= hora_atual < bl.data_hora_fim:
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
        return apps.get_model('app_shivazen', name)


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
        return f'Bloqueio {prof} @ {ini}'


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
