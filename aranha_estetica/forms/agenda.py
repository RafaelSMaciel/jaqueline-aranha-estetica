"""Forms da agenda do painel: novo agendamento pela recepcao e valor cobrado.

A tela de novo agendamento tem etapas (buscar cliente, ver horarios, agendar) num POST so; o Form
valida tudo no "agendar" e, nas outras etapas, so le o que ja da para mostrar (``lido``).
"""
from datetime import datetime

from django import forms
from django.utils import timezone

from ..excecoes import RegraDeNegocio
from ..models import Procedimento, Profissional
from ..services import agenda
from ..services.disponibilidade import profissionais_para
from .painel import CampoPreco, EscolhaPorId

MSG_HORARIO = 'Escolha a data e um dos horários livres.'
MSG_PROFISSIONAL = 'Selecione um profissional que realize o procedimento.'


class AgendamentoPainelForm(forms.Form):
    """Procedimento, profissional, dia, horario e valor combinado do agendamento pela recepcao.

    Nomes do POST da tela mantidos (procedimento_id, profissional_id, data, hora, valor) por
    ``add_prefix``. Profissional so vale se realiza o procedimento (profissionais_para). ``clean``
    monta o inicio no fuso da clinica e passa pela regra de horario do site (agenda.validar_horario:
    expediente, folga, bloqueio, buffer, antecedencia), com o motivo da recusa p/ a equipe —
    ``checar_horario=False`` pula essa etapa (a cliente ja foi recusada: um erro de cada vez)."""

    NOMES_POST = {'procedimento': 'procedimento_id', 'profissional': 'profissional_id'}

    procedimento = EscolhaPorId(queryset=Procedimento.objects.none(), error_messages={
        'required': 'Selecione o procedimento.', 'invalid_choice': 'Selecione o procedimento.'})
    profissional = EscolhaPorId(queryset=Profissional.objects.none(), error_messages={
        'required': MSG_PROFISSIONAL, 'invalid_choice': MSG_PROFISSIONAL})
    data = forms.DateField(input_formats=['%Y-%m-%d'], error_messages={'required': MSG_HORARIO,
                                                                       'invalid': MSG_HORARIO})
    hora = forms.TimeField(input_formats=['%H:%M', '%H:%M:%S'], error_messages={'required': MSG_HORARIO,
                                                                                'invalid': MSG_HORARIO})
    valor = CampoPreco(required=False)

    def __init__(self, *args, checar_horario: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.checar_horario = checar_horario
        self._lidos: dict = {}
        self.fields['procedimento'].queryset = Procedimento.objects.filter(ativo=True).order_by('nome')
        procedimento = self.lido('procedimento')
        self.fields['profissional'].queryset = (
            profissionais_para(procedimento) if procedimento
            else Profissional.objects.filter(ativo=True).order_by('nome')
        )

    def add_prefix(self, field_name):
        return self.NOMES_POST.get(field_name, field_name)

    def lido(self, nome):
        """Valor do campo sem levantar erro (None se vazio ou invalido) — p/ as etapas da tela que
        so re-renderizam (busca, horarios livres)."""
        if nome not in self._lidos:
            campo = self.fields[nome]
            bruto = campo.widget.value_from_datadict(self.data, self.files, self.add_prefix(nome))
            try:
                self._lidos[nome] = campo.clean(bruto)
            except forms.ValidationError:
                self._lidos[nome] = None
        return self._lidos[nome]

    def clean(self):
        dados = super().clean()
        if self.errors or not self.checar_horario:
            return dados
        inicio = timezone.make_aware(datetime.combine(dados['data'], dados['hora']))
        try:
            # habilitacao ja veio do queryset (profissionais_para)
            agenda.validar_horario(dados['profissional'], dados['procedimento'], inicio,
                                   checar_habilitacao=False, detalhado=True)
        except RegraDeNegocio as recusa:
            raise forms.ValidationError(recusa.mensagem, code=recusa.codigo) from recusa
        dados['inicio'] = inicio
        return dados


class ValorAtendimentoForm(forms.Form):
    """'Registrar valor' da agenda: o valor cobrado (CampoPreco: '150,00'/'150.00', com teto) e se
    abate o credito de indicacao da cliente. As regras do valor ficam em
    services.atendimento.registrar_valor_cobrado."""
    valor = CampoPreco(error_messages={'required': CampoPreco.default_error_messages['invalid']})
    usar_credito = forms.BooleanField(required=False)
