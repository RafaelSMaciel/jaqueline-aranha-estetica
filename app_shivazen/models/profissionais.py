# app_shivazen/models/profissionais.py — Profissionais, disponibilidade, bloqueios
from datetime import datetime, timedelta

from django.db import models


class Profissional(models.Model):
    nome = models.CharField(max_length=100)
    especialidade = models.TextField(blank=True, null=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'profissional'
        indexes = [
            models.Index(fields=['ativo'], name='idx_profissional_ativo'),
        ]

    def __str__(self):
        return self.nome

    def get_horarios_disponiveis(self, data_selecionada):
        from django.utils import timezone

        # Feriados/recessos bloqueiam o dia inteiro — evita agendar em 25/12, 01/01, etc.
        Feriado = self._get_model('Feriado')
        if Feriado.objects.filter(data=data_selecionada, bloqueia_agendamento=True).exists():
            return []

        dia_semana = data_selecionada.isoweekday() % 7 + 1
        disponibilidades = DisponibilidadeProfissional.objects.filter(
            profissional=self,
            dia_semana=dia_semana
        )
        if not disponibilidades.exists():
            return []

        agendamentos = self._get_model('Atendimento').objects.filter(
            profissional=self,
            data_hora_inicio__date=data_selecionada,
            status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
        )
        bloqueios = BloqueioAgenda.objects.filter(
            profissional=self,
            data_hora_inicio__date__lte=data_selecionada,
            data_hora_fim__date__gte=data_selecionada
        )

        horarios_disponiveis = []
        intervalo = timedelta(minutes=30)

        for disponibilidade in disponibilidades:
            hora_atual = datetime.combine(data_selecionada, disponibilidade.hora_inicio)
            hora_fim_expediente = datetime.combine(data_selecionada, disponibilidade.hora_fim)

            while hora_atual < hora_fim_expediente:
                horario_ocupado = False
                for ag in agendamentos:
                    if hora_atual >= ag.data_hora_inicio and hora_atual < ag.data_hora_fim:
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
