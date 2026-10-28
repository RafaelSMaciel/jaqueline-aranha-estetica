"""Forms do financeiro do painel: catalogo e compra de pacote, regra de comissao e repasse.

Mesma convencao de forms/painel: a view valida pelo Form e, se invalido, ``avisar_erros`` +
redirect. Antes criar e editar pacote tinham parsers proprios e uma mensagem generica ("Dados
invalidos: verifique preco, validade e itens"); aqui cada campo diz o que esta errado.
"""
from __future__ import annotations

from decimal import Decimal

from django import forms

from ..models import Procedimento, Profissional, RegraComissao
from ..utils.datas import hoje
from ..utils.parse import id_int
from .painel import CampoPreco

MSG_SEM_ITENS = 'Inclua ao menos um procedimento no pacote.'
MAX_SESSOES_ITEM = 999   # qtd_sessoes e smallint: '99999' estourava no banco


class PacoteForm(forms.Form):
    """Catalogo de pacote (criar e editar). Itens: as listas paralelas ``procedimento_ids`` e
    ``quantidades`` do modal (as linhas sao clonadas no navegador) viram ``cleaned_data['itens']`` =
    [(procedimento_id, qtd)] — ao menos 1, sem procedimento repetido (ItemPacote e unico por
    pacote+procedimento). Na edicao, POST sem a lista mantem os itens atuais (itens = None)."""
    nome = forms.CharField(max_length=150, error_messages={
        'required': 'Nome do pacote é obrigatório.',
        'max_length': 'Nome do pacote com no máximo 150 caracteres.',
    })
    descricao = forms.CharField(required=False)
    preco_total = CampoPreco(error_messages={
        'required': 'Informe o preço do pacote.', 'invalid': 'Preço inválido: use números, ex.: 1350,00.',
    })
    validade_meses = forms.IntegerField(min_value=1, max_value=120, error_messages={
        'required': 'Informe a validade em meses.', 'invalid': 'Validade de 1 a 120 meses.',
        'min_value': 'Validade de 1 a 120 meses.', 'max_value': 'Validade de 1 a 120 meses.',
    })
    ativo = forms.BooleanField(required=False)

    def __init__(self, *args, editando: bool = False, **kwargs):
        self.editando = editando
        super().__init__(*args, **kwargs)

    def clean_nome(self):
        nome = self.cleaned_data['nome'].strip()
        if not nome:
            raise forms.ValidationError('Nome do pacote é obrigatório.')
        return nome

    def clean_descricao(self):
        return (self.cleaned_data.get('descricao') or '').strip()

    def clean(self):
        dados = super().clean()
        if self.editando and 'procedimento_ids' not in self.data:
            dados['itens'] = None      # form sem a lista de itens: mantem os atuais
            return dados
        try:
            dados['itens'] = self._itens()
        except forms.ValidationError as erro:
            self.add_error(None, erro)
        return dados

    def _itens(self) -> list[tuple[int, int]]:
        itens, vistos = [], set()
        for proc_id, qtd in zip(self.data.getlist('procedimento_ids'), self.data.getlist('quantidades'),
                                strict=False):
            if not proc_id:
                continue    # linha em branco do modal
            pid, quantidade = id_int(proc_id), id_int(qtd)
            if pid is None:
                raise forms.ValidationError('Procedimento inválido no pacote.')
            if quantidade is None or not 1 <= quantidade <= MAX_SESSOES_ITEM:
                raise forms.ValidationError(f'Quantidade de sessões de 1 a {MAX_SESSOES_ITEM}.')
            if pid in vistos:
                raise forms.ValidationError('O mesmo procedimento aparece duas vezes: some as sessões numa linha.')
            vistos.add(pid)
            itens.append((pid, quantidade))
        if not itens:
            raise forms.ValidationError(MSG_SEM_ITENS)
        if set(Procedimento.objects.filter(pk__in=vistos).values_list('pk', flat=True)) != vistos:
            raise forms.ValidationError('Procedimento inválido no pacote.')
        return itens


class CancelarCompraForm(forms.Form):
    """Cancelamento da compra: valor devolvido em dinheiro (0 a valor pago) e o motivo/acordo."""
    valor_reembolsado = CampoPreco(error_messages={
        'required': 'Informe o valor devolvido à cliente (0 se nada foi devolvido).',
        'invalid': 'Valor devolvido inválido: use números, ex.: 150,00.',
    })
    observacao = forms.CharField(max_length=500, error_messages={
        'required': 'Descreva o motivo e o reembolso/acordo para cancelar o pacote.',
        'max_length': 'Motivo com no máximo 500 caracteres.',
    })

    def __init__(self, *args, compra, **kwargs):
        self.compra = compra
        super().__init__(*args, **kwargs)

    def clean_observacao(self):
        texto = self.cleaned_data['observacao'].strip()
        if not texto:
            raise forms.ValidationError('Descreva o motivo e o reembolso/acordo para cancelar o pacote.')
        return texto

    def clean_valor_reembolsado(self):
        from ..utils.formatos import formatar_brl

        valor = self.cleaned_data['valor_reembolsado']
        if valor > self.compra.valor_pago:
            raise forms.ValidationError(
                f'O valor devolvido não pode passar do valor pago ({formatar_brl(self.compra.valor_pago)}).')
        return valor


class ProrrogarValidadeForm(forms.Form):
    nova_data = forms.DateField(error_messages={
        'required': 'Informe a nova validade.', 'invalid': 'Data inválida.',
    })
    motivo = forms.CharField(max_length=500, error_messages={
        'required': 'Descreva o motivo da prorrogação.', 'max_length': 'Motivo com no máximo 500 caracteres.',
    })

    def clean_motivo(self):
        texto = self.cleaned_data['motivo'].strip()
        if not texto:
            raise forms.ValidationError('Descreva o motivo da prorrogação.')
        return texto


class RegraComissaoForm(forms.Form):
    """Regra nova: profissional e procedimento opcionais (vazio = qualquer), percentual XOR valor fixo.
    Nao ha edicao de regra: mudar o percentual = regra nova (e desativar a antiga)."""
    profissional = forms.ModelChoiceField(queryset=Profissional.objects.none(), required=False,
                                          error_messages={'invalid_choice': 'Profissional inválida.'})
    procedimento = forms.ModelChoiceField(queryset=Procedimento.objects.none(), required=False,
                                          error_messages={'invalid_choice': 'Procedimento inválido.'})
    percentual = forms.DecimalField(required=False, min_value=Decimal('0.01'), max_value=Decimal('100'),
                                    max_digits=5, decimal_places=2, error_messages={
                                        'invalid': 'Percentual inválido.', 'min_value': 'Percentual de 0,01 a 100.',
                                        'max_value': 'Percentual de 0,01 a 100.',
                                        'max_decimal_places': 'Percentual com até 2 casas decimais.',
                                        'max_digits': 'Percentual de 0,01 a 100.'})
    valor = CampoPreco(required=False, error_messages={'invalid': 'Valor fixo inválido: use números, ex.: 50,00.'})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['profissional'].queryset = Profissional.objects.filter(ativo=True)
        self.fields['procedimento'].queryset = Procedimento.objects.filter(ativo=True)
        # o painel manda "30,5": o DecimalField do Django so aceita ponto
        if self.is_bound and 'percentual' in self.data:
            dados = self.data.copy()
            dados['percentual'] = (dados.get('percentual') or '').replace(',', '.').strip()
            self.data = dados

    def clean(self):
        dados = super().clean()
        percentual, valor = dados.get('percentual'), dados.get('valor')
        if 'percentual' in self.errors or 'valor' in self.errors:
            return dados
        if (percentual is None) == (valor is None):
            raise forms.ValidationError('Informe o percentual OU o valor fixo da comissão (só um dos dois).')
        if valor is not None and valor <= 0:
            raise forms.ValidationError('Valor fixo da comissão maior que zero.')
        return dados

    def salvar(self) -> RegraComissao:
        dados = self.cleaned_data
        return RegraComissao.objects.create(
            profissional=dados.get('profissional'), procedimento=dados.get('procedimento'),
            percentual=dados.get('percentual'), valor=dados.get('valor'), ativo=True,
        )


class RepasseForm(forms.Form):
    """'Pagar pendentes ate <data>' de uma profissional (ativa ou nao: quem saiu tambem recebe)."""
    profissional = forms.ModelChoiceField(queryset=Profissional.objects.all(), error_messages={
        'required': 'Escolha a profissional do repasse.', 'invalid_choice': 'Profissional inválida.',
    })
    ate = forms.DateField(error_messages={'required': 'Informe até que data pagar.', 'invalid': 'Data inválida.'})

    def clean_ate(self):
        ate = self.cleaned_data['ate']
        if ate > hoje():
            raise forms.ValidationError('O repasse vai até hoje, no máximo (atendimento futuro não tem comissão).')
        return ate
