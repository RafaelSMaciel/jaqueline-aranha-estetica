"""Form do pedido de agendamento pelo site (POST do passo 3 do wizard).

Adaptador de entrada do caso de uso ``services.agendamento_online.solicitar``: le os campos do
POST (mesmos nomes de sempre), normaliza, valida formato e o gate do OTP (o celular tem de ser o
verificado por SMS NESTA sessao) e entrega um ``PedidoAgendamento``. Regra de negocio (horario,
fichas, termos, cadastro) fica no service.

A tela mostra um erro por vez (``primeiro_erro``), na ordem de sempre: campo obrigatorio vazio,
Politica de Privacidade, celular, verificacao por SMS, e-mail, nascimento/idade, selecao, nome.
O wizard valida os mesmos formatos antes de enviar (static/src/js/wizard.js) — aqui e a garantia.

Varios tratamentos no mesmo pedido: ``procedimento``/``profissional``/``datetime`` sao o 1o e
``itens_extras`` (JSON: [{"procedimento", "profissional", "datetime"}]) os demais.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from ..constants import MAX_TRATAMENTOS_POR_PEDIDO
from ..models import Cliente, Procedimento, Profissional
from ..services import sessao_cliente
from ..services.agendamento_online import ItemPedido, PedidoAgendamento
from ..utils import telefone as telefone_br
from ..utils.datas import hoje as hoje_local, ler_data
from ..utils.parse import id_int
from ..validators import normalizar_nome, validate_data_nascimento, validate_nome_completo

logger = logging.getLogger(__name__)

IDADE_MINIMA = 18
IDADE_MAXIMA = 120
# Teto do JSON dos tratamentos extras do pedido — acima disso e abuso.
MAX_ITENS_BYTES = 2_000
# Teto do JSON da anamnese (todos os formularios somados) — acima disso e abuso.
MAX_ANAMNESE_BYTES = 20_000
PREFIXO_TERMO = 'aceite_termo_'

MSG_CAMPOS = 'Preencha todos os campos obrigatórios.'
MSG_POLITICA = 'Para agendar, confirme que leu e aceita a Política de Privacidade.'
MSG_CELULAR = 'Informe um celular válido com DDD, ex.: (11) 99999-9999.'
MSG_SEM_OTP = 'Confirme seu celular com o código enviado por SMS antes de concluir.'
MSG_EMAIL = 'Informe um e-mail válido ou deixe o campo em branco.'
MSG_NASCIMENTO = 'Data de nascimento inválida. Confira o dia, o mês e o ano.'
MSG_IDADE = 'É necessário ter pelo menos 18 anos para agendar.'
MSG_SELECAO = 'Dados do agendamento inválidos. Refaça a seleção.'
MSG_ANAMNESE_LONGA = 'As respostas do questionário ficaram longas demais. Resuma e tente novamente.'


class Marcado(forms.Field):
    """Checkbox do wizard: marcado so com o valor que o navegador manda (``on``, ou ``valor``)."""

    def __init__(self, *, valor: str = 'on', **kwargs):
        self.valor = valor
        kwargs.setdefault('required', False)
        super().__init__(**kwargs)

    def to_python(self, value) -> bool:
        return value == self.valor


def _idade(nascimento, hoje) -> int:
    return hoje.year - nascimento.year - ((hoje.month, hoje.day) < (nascimento.month, nascimento.day))


class AgendamentoPublicoForm(forms.Form):
    """Campos do POST /agendamento/confirmar/ (sem login: identidade = celular verificado)."""

    # Ordem = ordem de exibicao do primeiro erro (depois do "campo obrigatorio vazio")
    aceite_politica = Marcado()
    telefone = forms.CharField(error_messages={'required': MSG_CAMPOS})
    email = forms.CharField(required=False)
    data_nascimento = forms.CharField(error_messages={'required': MSG_CAMPOS})
    procedimento = forms.CharField(error_messages={'required': MSG_CAMPOS})
    profissional = forms.CharField(error_messages={'required': MSG_CAMPOS})
    datetime = forms.CharField(error_messages={'required': MSG_CAMPOS})
    itens_extras = forms.CharField(required=False)
    nome = forms.CharField(error_messages={'required': MSG_CAMPOS})
    anamnese_respostas = forms.CharField(required=False)
    consent_dados_saude = Marcado()

    def __init__(self, data=None, *, request, **kwargs):
        super().__init__(data, **kwargs)
        self.request = request

    # ─── campos ───

    def clean_aceite_politica(self) -> bool:
        if not self.cleaned_data.get('aceite_politica'):
            raise ValidationError(MSG_POLITICA, code='politica')
        return True

    def clean_nome(self) -> str:
        nome = normalizar_nome(self.cleaned_data['nome'])[:150]
        # Cadastro existente: o nome do POST e ignorado (D16 — vale o do cadastro), entao nao e
        # validado: uma titular antiga com o nome so pela metade nao fica sem conseguir agendar
        telefone = self.cleaned_data.get('telefone')
        if telefone and Cliente.objects.filter(telefone=telefone).exists():
            return nome
        validate_nome_completo(nome)
        return nome

    def clean_telefone(self) -> str:
        telefone = telefone_br.valido_ou_vazio(self.cleaned_data['telefone'])
        if not telefone:
            raise ValidationError(MSG_CELULAR, code='celular')
        # Gate unico: o telefone do agendamento precisa ser o verificado por SMS nesta sessao
        # (vale p/ cliente novo e recorrente; e-mail nunca e identidade).
        if sessao_cliente.telefone_para_agendamento(self.request) != telefone:
            raise ValidationError(MSG_SEM_OTP, code='sem_otp')
        return telefone

    def clean_email(self) -> str | None:
        email = self.cleaned_data.get('email', '').lower() or None
        if email:
            try:
                validate_email(email)
            except ValidationError:
                raise ValidationError(MSG_EMAIL, code='email') from None
        return email

    def clean_data_nascimento(self):
        try:
            nascimento = ler_data(self.cleaned_data['data_nascimento'])
            validate_data_nascimento(nascimento)
        except (ValueError, ValidationError):
            raise ValidationError(MSG_NASCIMENTO, code='nascimento') from None
        idade = _idade(nascimento, hoje_local())
        if idade < IDADE_MINIMA:
            raise ValidationError(MSG_IDADE, code='idade')
        if idade > IDADE_MAXIMA:
            raise ValidationError(MSG_NASCIMENTO, code='nascimento')
        return nascimento

    def _ativo(self, model, campo):
        pk = id_int(self.cleaned_data[campo])
        obj = model.objects.filter(pk=pk, ativo=True).first() if pk is not None else None
        if obj is None:
            raise ValidationError(MSG_SELECAO, code='selecao')
        return obj

    def clean_procedimento(self) -> Procedimento:
        return self._ativo(Procedimento, 'procedimento')

    def clean_profissional(self) -> Profissional:
        return self._ativo(Profissional, 'profissional')

    @staticmethod
    def _instante(valor) -> dt.datetime:
        try:
            inicio = dt.datetime.fromisoformat(str(valor))
            if timezone.is_naive(inicio):
                inicio = timezone.make_aware(inicio)
            return timezone.localtime(inicio)
        except (ValueError, TypeError, OverflowError):
            raise ValidationError(MSG_SELECAO, code='selecao') from None

    def clean_datetime(self) -> dt.datetime:
        return self._instante(self.cleaned_data['datetime'])

    def clean_itens_extras(self) -> tuple[ItemPedido, ...]:
        """Os outros tratamentos do pedido (alem do 1o). Lista malformada, item incompleto ou
        acima do teto recusa o pedido inteiro — nada de agendar "so o que deu p/ entender"."""
        bruto = self.cleaned_data.get('itens_extras', '')
        if not bruto:
            return ()
        if len(bruto) > MAX_ITENS_BYTES:
            raise ValidationError(MSG_SELECAO, code='selecao')
        try:
            lista = json.loads(bruto)
        except (TypeError, ValueError):
            raise ValidationError(MSG_SELECAO, code='selecao') from None
        if not isinstance(lista, list) or len(lista) > MAX_TRATAMENTOS_POR_PEDIDO - 1:
            raise ValidationError(MSG_SELECAO, code='selecao')
        itens = []
        for bruto_item in lista:
            if not isinstance(bruto_item, dict):
                raise ValidationError(MSG_SELECAO, code='selecao')
            proc_id, prof_id = id_int(bruto_item.get('procedimento')), id_int(bruto_item.get('profissional'))
            procedimento = Procedimento.objects.filter(pk=proc_id, ativo=True).first() if proc_id is not None else None
            profissional = Profissional.objects.filter(pk=prof_id, ativo=True).first() if prof_id is not None else None
            if procedimento is None or profissional is None:
                raise ValidationError(MSG_SELECAO, code='selecao')
            itens.append(ItemPedido(procedimento, profissional, self._instante(bruto_item.get('datetime'))))
        return tuple(itens)

    def clean_anamnese_respostas(self) -> dict:
        bruto = self.cleaned_data.get('anamnese_respostas', '')
        if len(bruto) > MAX_ANAMNESE_BYTES:
            raise ValidationError(MSG_ANAMNESE_LONGA, code='anamnese_longa')
        try:
            dados = json.loads(bruto) if bruto else {}
        except (TypeError, ValueError):
            logger.warning('booking_anamnese_json_invalido')
            dados = {}
        return dados if isinstance(dados, dict) else {}

    # ─── saida ───

    def termos_marcados(self) -> frozenset[int]:
        """Ids das VersaoTermo com o checkbox ``aceite_termo_<id>`` marcado."""
        marcados = set()
        for chave, valor in self.data.items():
            if chave.startswith(PREFIXO_TERMO) and valor == 'on':
                pk = id_int(chave[len(PREFIXO_TERMO):])
                if pk is not None:
                    marcados.add(pk)
        return frozenset(marcados)

    def primeiro_erro(self) -> str:
        """A mensagem que a tela mostra: campo obrigatorio vazio primeiro, depois o 1o erro na
        ordem dos campos."""
        erros = self.errors.as_data()
        if any(erro.code == 'required' for lista in erros.values() for erro in lista):
            return MSG_CAMPOS
        for lista in erros.values():
            return lista[0].messages[0]
        return MSG_SELECAO

    def pedido(self) -> PedidoAgendamento:
        """O pedido p/ ``agendamento_online.solicitar`` (so depois de ``is_valid()``)."""
        dados = self.cleaned_data
        return PedidoAgendamento(
            nome=dados['nome'],
            telefone=dados['telefone'],
            data_nascimento=dados['data_nascimento'],
            email=dados['email'],
            procedimento=dados['procedimento'],
            profissional=dados['profissional'],
            inicio=dados['datetime'],
            extras=dados['itens_extras'],
            termos_marcados=self.termos_marcados(),
            anamneses=dados['anamnese_respostas'],
            consent_saude=dados['consent_dados_saude'],
        )
