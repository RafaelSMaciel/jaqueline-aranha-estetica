"""Form da promocao (criar e editar usam o mesmo, com ``instance=``).

Regras (REGRAS §13): desconto percentual (0 < d <= 100) XOR preco fixo; promocao geral (sem
procedimento) so por percentual (Promocao.clean); fim >= inicio. O banco repete as tres em CHECK.
"""
from decimal import Decimal, InvalidOperation

from django import forms

from ..models import Procedimento, Promocao
from .painel import CampoPreco

MSG_DESCONTO_OU_PRECO = 'Informe o desconto (até 100%) ou o preço fixo.'
MSG_PERIODO = 'A data de fim deve ser igual ou posterior à de início.'
CEM = Decimal('100')


class CampoPercentual(forms.CharField):
    """'15', '15,5' ou '15.50' -> Decimal em (0, 100] com 2 casas; vazio = None. (O DecimalField
    nao aceita virgula; com localize, '12.5' do input number viraria 125 pelo separador de milhar.)"""
    default_error_messages = {'invalid': MSG_DESCONTO_OU_PRECO}

    def to_python(self, value):
        valor = (super().to_python(value) or '').strip()
        if not valor:
            return None
        try:
            percentual = Decimal(valor.replace(',', '.'))
        except InvalidOperation as exc:
            raise forms.ValidationError(self.error_messages['invalid'], code='invalid') from exc
        if not percentual.is_finite() or not 0 < percentual <= CEM:
            raise forms.ValidationError(self.error_messages['invalid'], code='invalid')
        return percentual.quantize(Decimal('0.01'))


class PromocaoForm(forms.ModelForm):
    # vazio = promocao geral (todos os procedimentos); inativo continua valido (edicao nao troca calada)
    procedimento = forms.ModelChoiceField(
        queryset=Procedimento.objects.all(), required=False, label='Procedimento',
        error_messages={'invalid_choice': 'Procedimento inválido.'},
    )
    desconto = CampoPercentual(required=False, label='Desconto')
    preco_promocional = CampoPreco(required=False, label='Preço fixo')
    # select Sim/Nao da tela ('1'/'0'): o BooleanField leria '0' como verdadeiro
    ativa = forms.CharField(required=False)

    class Meta:
        model = Promocao
        fields = ['nome', 'descricao', 'procedimento', 'preco_promocional', 'data_inicio', 'data_fim', 'ativa']
        labels = {'nome': 'Nome', 'descricao': 'Descrição', 'data_inicio': 'Data de início', 'data_fim': 'Data de fim'}
        error_messages = {
            'nome': {'required': 'Informe o nome da promoção.'},
            'data_inicio': {'required': 'Informe as datas da promoção.', 'invalid': 'Data inválida.'},
            'data_fim': {'required': 'Informe as datas da promoção.', 'invalid': 'Data inválida.'},
        }

    def clean_ativa(self) -> bool:
        return self.cleaned_data.get('ativa') == '1'

    def clean(self):
        dados = super().clean()
        inicio, fim = dados.get('data_inicio'), dados.get('data_fim')
        if inicio and fim and fim < inicio:
            self.add_error('data_fim', MSG_PERIODO)
        preco, desconto = dados.get('preco_promocional'), dados.get('desconto')
        if 'desconto' in self.errors or 'preco_promocional' in self.errors:
            return dados
        if (preco is None) == (desconto is None):
            raise forms.ValidationError(MSG_DESCONTO_OU_PRECO)
        # o desconto nao e campo do form (nome 'desconto' na tela): vai direto p/ a instancia
        self.instance.desconto_percentual = Decimal('0') if preco is not None else desconto
        return dados
