"""Form do pedido de agendamento pelo site (POST do passo 3 do wizard).

Adaptador de entrada do caso de uso ``services.agendamento_online.solicitar``: le os campos do
POST (mesmos nomes de sempre), normaliza, valida formato e o gate do OTP (o celular tem de ser o
verificado por SMS NESTA sessao) e entrega um ``PedidoAgendamento``. Regra de negocio (horario,
fichas, termos, cadastro) fica no service.

A tela mostra um erro por vez (``primeiro_erro``), na ordem de sempre: campo obrigatorio vazio,
Politica de Privacidade, celular, verificacao por SMS, e-mail, nascimento/idade, selecao.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from ..models import Procedimento, Profissional
from ..services import consentimentos
from ..services import otp as otp_service
from ..services.agendamento_online import PedidoAgendamento
from ..utils.datas import hoje as hoje_local, ler_data
from ..utils.parse import id_int
from ..validators import validate_data_nascimento

logger = logging.getLogger(__name__)

IDADE_MINIMA = 18
# Teto do JSON da anamnese (todos os formularios somados) — acima disso e abuso.
MAX_ANAMNESE_BYTES = 20_000
PREFIXO_TERMO = 'aceite_termo_'

MSG_CAMPOS = 'Preencha todos os campos obrigatórios.'
MSG_POLITICA = 'Para agendar, confirme que leu e aceita a Política de Privacidade.'
MSG_CELULAR = 'Informe um celular válido com DDD, ex.: (17) 99999-9999.'
MSG_SEM_OTP = 'Confirme seu celular com o código enviado por SMS antes de concluir.'
MSG_EMAIL = 'Informe um e-mail válido ou deixe o campo em branco.'
MSG_NASCIMENTO = 'Data de nascimento inválida.'
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
    nome = forms.CharField(error_messages={'required': MSG_CAMPOS})
    anamnese_respostas = forms.CharField(required=False)
    consent_dados_saude = Marcado()
    # '1' = os checkboxes de comunicacao mostravam o estado do cadastro (prefill do OTP)
    consents_sincronizados = Marcado(valor='1')

    def __init__(self, data=None, *, request, **kwargs):
        super().__init__(data, **kwargs)
        self.request = request
        # Opt-ins de comunicacao: checkbox do wizard -> par finalidade x canal
        for campo in consentimentos.PARES:
            self.fields[campo] = Marcado()

    # ─── campos ───

    def clean_aceite_politica(self) -> bool:
        if not self.cleaned_data.get('aceite_politica'):
            raise ValidationError(MSG_POLITICA, code='politica')
        return True

    def clean_nome(self) -> str:
        return self.cleaned_data['nome'][:150]

    def clean_telefone(self) -> str:
        telefone = otp_service.normalizar_telefone_br(self.cleaned_data['telefone'])
        if not telefone:
            raise ValidationError(MSG_CELULAR, code='celular')
        # Gate unico: o telefone do agendamento precisa ser o verificado por SMS nesta sessao
        # (vale p/ cliente novo e recorrente; e-mail nunca e identidade).
        if otp_service.telefone_verificado_agendamento(self.request) != telefone:
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
        if _idade(nascimento, hoje_local()) < IDADE_MINIMA:
            raise ValidationError(MSG_IDADE, code='idade')
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

    def clean_datetime(self) -> dt.datetime:
        try:
            inicio = dt.datetime.fromisoformat(self.cleaned_data['datetime'])
            if timezone.is_naive(inicio):
                inicio = timezone.make_aware(inicio)
            return timezone.localtime(inicio)
        except (ValueError, TypeError, OverflowError):
            raise ValidationError(MSG_SELECAO, code='selecao') from None

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
            consents={campo: dados[campo] for campo in consentimentos.PARES},
            consents_sincronizados=dados['consents_sincronizados'],
            termos_marcados=self.termos_marcados(),
            anamneses=dados['anamnese_respostas'],
            consent_saude=dados['consent_dados_saude'],
        )
