"""Forms da agenda do painel: novo agendamento pela recepcao, valor cobrado e excecoes de agenda.

A tela de novo agendamento tem etapas (buscar cliente, ver horarios, agendar) num POST so; o Form
valida tudo no "agendar" e, nas outras etapas, so le o que ja da para mostrar (``lido``).
"""
from datetime import datetime, timedelta

from django import forms
from django.utils import timezone

from ..excecoes import RegraDeNegocio
from ..models import AgendaExcecao, Procedimento, Profissional
from ..services import agenda
from ..services.disponibilidade import profissionais_para
from ..utils.datas import inicio_do_dia
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


# Tipos que a tela de excecoes do profissional cria (bloqueio tem tela propria; feriado/recesso
# vem do carregar_feriados / Django admin).
TIPOS_PAINEL = [
    (AgendaExcecao.TIPO_FOLGA, 'Folga'),
    (AgendaExcecao.TIPO_HORARIO_DIFERENTE, 'Horário diferente'),
]
MSG_DATA_E_TIPO = 'Data e tipo são obrigatórios.'
FORMATOS_HORA = ['%H:%M', '%H:%M:%S']


class AgendaExcecaoForm(forms.ModelForm):
    """Folga (um ou varios dias) ou horario diferente (um dia) de um profissional.

    As mensagens da tela ficam aqui (campo a campo); as regras do model (AgendaExcecao.clean:
    espelho dos CHECKs e da folga sobreposta) rodam depois, so se o form passou. Reenviar o mesmo
    dia/periodo atualiza a excecao vigente (AgendaExcecao.objects.vigente_para) em vez de duplicar.
    Campo do POST 'data' = data_inicio."""

    NOMES_POST = {'data_inicio': 'data'}

    tipo = forms.ChoiceField(choices=TIPOS_PAINEL, error_messages={'required': MSG_DATA_E_TIPO,
                                                                   'invalid_choice': MSG_DATA_E_TIPO})
    data_inicio = forms.DateField(input_formats=['%Y-%m-%d'], error_messages={'required': MSG_DATA_E_TIPO,
                                                                              'invalid': 'Data inválida.'})
    data_fim = forms.DateField(required=False, input_formats=['%Y-%m-%d'], error_messages={'invalid': 'Data inválida.'})
    hora_inicio = forms.TimeField(required=False, input_formats=FORMATOS_HORA, error_messages={'invalid': 'Hora inválida.'})
    hora_fim = forms.TimeField(required=False, input_formats=FORMATOS_HORA, error_messages={'invalid': 'Hora inválida.'})
    motivo = forms.CharField(required=False, strip=True)

    class Meta:
        model = AgendaExcecao
        fields = ['tipo', 'data_inicio', 'data_fim', 'hora_inicio', 'hora_fim', 'motivo']

    def __init__(self, *args, profissional, autor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.profissional = profissional
        self.autor = autor
        self.criada = True

    def add_prefix(self, field_name):
        return self.NOMES_POST.get(field_name, field_name)

    def clean(self):
        dados = super().clean()
        tipo, data = dados.get('tipo'), dados.get('data_inicio')
        if not tipo or not data or self.errors:
            return dados
        if tipo == AgendaExcecao.TIPO_HORARIO_DIFERENTE:
            self._limpar_horario(dados)
            dados['data_fim'] = data   # horario diferente vale um dia
        else:
            dados['hora_inicio'] = dados['hora_fim'] = None
            dados['data_fim'] = dados.get('data_fim') or data
            if dados['data_fim'] < data:
                self.add_error('data_fim', 'A data final da folga deve ser igual ou posterior à inicial.')
        if not self.errors:
            self._recusar_marcados(tipo, data, dados)
        if not self.errors:
            self._usar_vigente(tipo, data, dados['data_fim'])
        return dados

    def _recusar_marcados(self, tipo, data, dados) -> None:
        """Folga (o dia inteiro) ou horario diferente (fora da nova janela) sobre atendimento ja
        marcado: recusa listando-os (services/agenda.marcados_no_periodo)."""
        marcados = agenda.marcados_no_periodo(
            self.profissional, inicio_do_dia(data), inicio_do_dia(dados['data_fim'] + timedelta(days=1)))
        if tipo == AgendaExcecao.TIPO_HORARIO_DIFERENTE:
            janela = (dados['hora_inicio'], dados['hora_fim'])
            marcados = [a for a in marcados
                        if not (janela[0] <= timezone.localtime(a.data_hora_inicio).time()
                                and timezone.localtime(a.data_hora_fim).time() <= janela[1])]
        if marcados:
            self.add_error(None, agenda.recusa_marcados(marcados))

    def _limpar_horario(self, dados) -> None:
        inicio, fim = dados.get('hora_inicio'), dados.get('hora_fim')
        if not inicio or not fim:
            raise forms.ValidationError('Para horário diferente, informe a hora de início e a de fim.')
        # sem isso uma janela 14:00-09:00 zerava a agenda do dia sem aviso
        if fim <= inicio:
            self.add_error('hora_fim', 'A hora de fim deve ser depois da hora de início.')

    def _usar_vigente(self, tipo, data, data_fim) -> None:
        """Upsert: o mesmo dia/periodo ja cadastrado vira a instancia do form (antes de validar no model)."""
        vigente = AgendaExcecao.objects.vigente_para(self.profissional, tipo, data, data_fim)
        if vigente is not None:
            self.instance, self.criada = vigente, False
        else:
            self.instance.profissional = self.profissional
            self.instance.origem = AgendaExcecao.ORIGEM_MANUAL
            self.instance.usuario_autor = self.autor

    def _post_clean(self):
        # Um aviso por vez, como a tela sempre fez: com erro no form, o model nem e validado
        # (a mesma regra sairia de novo com outra frase).
        if not self.errors:
            super()._post_clean()
