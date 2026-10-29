"""Forms das telas do painel (bloqueio de agenda, termo) e o que os forms do painel compartilham
(CampoPreco, avisar_erros, erros_por_campo). Cadastros (equipe, procedimento, regras): forms/cadastros.

Antes cada view lia ``request.POST.get`` campo a campo, com parse proprio (preco com virgula,
duracao, data ISO) e mensagem solta. Aqui a validacao e o Form; a view so faz:

    form = BloqueioForm(request.POST)
    if not form.is_valid():
        avisar_erros(request, form)
        return ...   # tela com modal: devolve a lista com o form aberto (ex.: procedimentos)

Mensagens iguais as de antes (os testes e a equipe ja conhecem); o aviso no topo leva o
rotulo do campo na frente ("Preço (R$): ..."), p/ dizer QUAL campo foi recusado (WCAG 3.3.1).
"""
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages

from ..models import AgendaExcecao, Procedimento, Profissional, VersaoTermo
from ..utils.datas import hoje

# Teto dos campos de valor (DecimalField max_digits=10, decimal_places=2)
VALOR_MAX = Decimal('99999999.99')
MSG_DURACAO_OU_PRECO = 'Duração ou preço inválido.'


def avisar_erros(request, form) -> None:
    """Cada erro do form vira um messages.error (a tela mostra no topo; sem repetir).

    Erro de campo sai com o rotulo do campo na frente ("Nome: Informe o nome..."), a nao ser que
    a mensagem ja comece por ele ("Versão deve ter..."); erro geral (clean() do form) sai como esta."""
    vistos = set()
    for campo, erros in form.errors.items():
        rotulo = form.fields[campo].label if campo in form.fields else None
        for erro in erros:
            erro = str(erro)
            texto = f'{rotulo}: {erro}' if rotulo and not erro.lower().startswith(rotulo.lower()) else erro
            if texto not in vistos:
                vistos.add(texto)
                messages.error(request, texto)


def erros_por_campo(form) -> dict:
    """{campo: primeira mensagem} p/ o template marcar o campo (aria-invalid + texto do erro)."""
    return {campo: erros[0] for campo, erros in form.errors.items() if erros}


class CampoPreco(forms.CharField):
    """'1350,50' / '1350.50' -> Decimal em [0, VALOR_MAX]; vazio = None.

    O teto e conferido ANTES do quantize: '1e30' estourava o contexto decimal e '123456789'
    so falhava no banco (numeric field overflow no Postgres)."""
    default_error_messages = {'invalid': 'Valor inválido: use números, ex.: 150,00.'}

    def to_python(self, value):
        valor = (super().to_python(value) or '').strip()
        if not valor:
            return None
        try:
            preco = Decimal(valor.replace(',', '.'))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise forms.ValidationError(self.error_messages['invalid'], code='invalid') from exc
        if not preco.is_finite() or preco < 0 or preco > VALOR_MAX:
            raise forms.ValidationError(self.error_messages['invalid'], code='invalid')
        return preco.quantize(Decimal('0.01'))


def parse_preco(valor):
    """Mesmo parse do CampoPreco p/ quem nao usa Form (agendamento pelo painel). ValueError se invalido."""
    try:
        return CampoPreco(required=False).clean(valor)
    except forms.ValidationError as exc:
        raise ValueError('preco invalido') from exc


# ProcedimentoForm (ModelForm) esta em forms/cadastros, com os demais cadastros do painel.


class BloqueioForm(forms.Form):
    """Bloqueio pontual de agenda (AgendaExcecao BLOQUEIO). profissional_id='todos' = global."""
    profissional_id = forms.CharField(label='Profissional')
    # ISO 8601 (o datetime-local da tela); sem fuso = fuso da clinica (o Form torna aware)
    data_hora_inicio = forms.DateTimeField(label='Início')
    data_hora_fim = forms.DateTimeField(label='Fim')
    motivo = forms.CharField(label='Motivo', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            campo.error_messages = {**campo.error_messages, 'required': 'Erro ao criar bloqueio.',
                                    'invalid': 'Erro ao criar bloqueio.'}

    def clean_profissional_id(self):
        valor = self.cleaned_data['profissional_id']
        if valor == 'todos':
            return None
        prof = Profissional.objects.filter(pk=valor).first() if valor.isascii() and valor.isdigit() else None
        if prof is None:
            raise forms.ValidationError('Erro ao criar bloqueio.')
        return prof

    def clean(self):
        dados = super().clean()
        inicio, fim = dados.get('data_hora_inicio'), dados.get('data_hora_fim')
        if inicio and fim and fim <= inicio:
            raise forms.ValidationError('Data fim deve ser posterior a data inicio.')
        return dados

    def criar(self, autor) -> AgendaExcecao:
        d = self.cleaned_data
        return AgendaExcecao.objects.create(
            tipo=AgendaExcecao.TIPO_BLOQUEIO, origem=AgendaExcecao.ORIGEM_MANUAL,
            profissional=d['profissional_id'], data_hora_inicio=d['data_hora_inicio'],
            data_hora_fim=d['data_hora_fim'], motivo=d['motivo'].strip(), usuario_autor=autor,
        )


class TermoForm(forms.Form):
    """Nova versao de termo (LGPD, SAUDE ou de PROCEDIMENTO)."""
    tipo = forms.ChoiceField(label='Tipo', choices=VersaoTermo.TIPO_CHOICES, required=False,
                             error_messages={'invalid_choice': 'Tipo de termo inválido.'})
    titulo = forms.CharField(label='Título', required=False)
    conteudo = forms.CharField(label='Conteúdo', required=False)
    versao = forms.CharField(label='Versão', required=False, max_length=20,
                             error_messages={'max_length': 'Versão deve ter no máximo 20 caracteres.'})
    procedimento_id = forms.CharField(label='Procedimento', required=False)
    vigente_desde = forms.DateField(label='Vigente desde', required=False, input_formats=['%Y-%m-%d'],
                                    error_messages={'invalid': 'Data de vigência inválida.'})

    def clean_tipo(self):
        return self.cleaned_data.get('tipo') or 'LGPD'

    def clean_versao(self):
        return (self.cleaned_data.get('versao') or '').strip() or '1.0'

    def clean_vigente_desde(self):
        return self.cleaned_data.get('vigente_desde') or hoje()

    def clean(self):
        dados = super().clean()
        dados['titulo'] = (dados.get('titulo') or '').strip()
        dados['conteudo'] = (dados.get('conteudo') or '').strip()
        if 'tipo' in self.errors:
            return dados
        if not dados['titulo'] or not dados['conteudo']:
            raise forms.ValidationError('Informe título e conteúdo do termo.')
        dados['procedimento'] = None
        bruto = (dados.get('procedimento_id') or '').strip()
        if dados.get('tipo') == 'PROCEDIMENTO' and bruto:
            # isascii: isdigit() aceitava '²' e o int() dava 500
            if not (bruto.isascii() and bruto.isdigit()):
                raise forms.ValidationError('Procedimento inválido.')
            dados['procedimento'] = Procedimento.objects.filter(pk=int(bruto)).first()
            if dados['procedimento'] is None:
                raise forms.ValidationError('Procedimento não encontrado.')
        return dados
