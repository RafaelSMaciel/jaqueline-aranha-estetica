import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, OperationalError, ProgrammingError, transaction
from django.shortcuts import redirect, render
from django_ratelimit.decorators import ratelimit

from ...models import (
    Cliente,
    ListaEspera,
    Procedimento,
    Profissional,
)
from ...services import otp as otp_service
from ...utils import datas
from ...utils import telefone as telefones
from ...utils.datas import ler_data
from ...utils.parse import id_int

logger = logging.getLogger(__name__)

TURNOS = [('MANHA', 'Manhã'), ('TARDE', 'Tarde'), ('NOITE', 'Noite')]


def _prefill_do_wizard(params) -> dict:
    """?procedimento=&profissional=&data= do wizard -> form_data (so ids e data validos)."""
    prefill = {campo: str(id_int(params.get(campo))) for campo in ('procedimento', 'profissional')
               if id_int(params.get(campo)) is not None}
    try:
        dia = ler_data(params.get('data', ''))
    except ValueError:
        dia = None
    if dia is not None and dia >= datas.hoje():
        prefill['data_desejada'] = dia.isoformat()
    return prefill


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def lista_espera_publica(request):
    """Formulario publico para cliente se inscrever na lista de espera.

    Form anonimo (sem OTP): NUNCA altera dados de um Cliente que ja existe —
    so cria o cadastro (sem e-mail) quando o telefone e novo. O e-mail digitado
    fica so na inscricao (ListaEspera.email_contato), e p/ telefone ja cadastrado
    apenas se o celular foi confirmado por SMS nesta sessao — senao qualquer um
    prenderia o proprio e-mail ao telefone de terceiros.
    """
    procedimentos = []
    profissionais = []
    try:
        procedimentos = list(
            Procedimento.objects.filter(ativo=True).order_by('nome')
        )
        profissionais = list(
            Profissional.objects.filter(ativo=True).order_by('nome')
        )
    except (OperationalError, ProgrammingError):
        logger.warning('Tabelas de procedimento/profissional nao encontradas.')

    context = {
        'procedimentos': procedimentos,
        'profissionais': profissionais,
        'turnos': TURNOS,
        'data_minima': datas.hoje().isoformat(),
        'form_data': {},
    }

    if request.method != 'POST':
        # Vindo do wizard ("Avise-me se vagar" num dia sem horario): ja chega preenchido
        context['form_data'] = _prefill_do_wizard(request.GET)
        return render(request, 'publico/lista_espera.html', context)

    nome = request.POST.get('nome', '').strip()[:150]
    email = request.POST.get('email', '').strip()[:254]
    telefone_bruto = request.POST.get('telefone', '').strip()
    telefone = telefones.valido_ou_vazio(telefone_bruto)
    procedimento_id = request.POST.get('procedimento', '')
    profissional_id = request.POST.get('profissional', '')
    data_desejada = request.POST.get('data_desejada', '')
    turno = request.POST.get('turno', '') or None

    context['form_data'] = {
        'nome': nome, 'email': email, 'telefone': telefone_bruto[:20],
        'procedimento': procedimento_id, 'profissional': profissional_id,
        'data_desejada': data_desejada, 'turno': turno or '',
    }

    def _erro(msg):
        messages.error(request, msg)
        return render(request, 'publico/lista_espera.html', context)

    if not all([nome, telefone_bruto, email, procedimento_id, data_desejada]):
        return _erro('Preencha nome, celular, e-mail, procedimento e data.')

    # Valida antes do banco: o CHECK do Postgres (10-11 digitos) viraria 500.
    if not telefone:
        return _erro('Informe um celular válido com DDD, ex.: (17) 99999-9999.')

    try:
        validate_email(email)
    except ValidationError:
        return _erro('Informe um e-mail válido.')

    try:
        procedimento = Procedimento.objects.get(pk=procedimento_id, ativo=True)
    except (Procedimento.DoesNotExist, ValueError):
        return _erro('Procedimento inválido.')

    profissional = None
    if profissional_id:
        try:
            profissional = Profissional.objects.get(pk=profissional_id, ativo=True)
        except (Profissional.DoesNotExist, ValueError):
            profissional = None

    try:
        data_obj = ler_data(data_desejada)
    except ValueError:
        return _erro('Data inválida.')

    if data_obj < datas.hoje():
        return _erro('Escolha uma data a partir de hoje.')

    if turno and turno not in {k for k, _ in TURNOS}:
        turno = None

    # Celular confirmado por SMS nesta sessao (wizard) = posse provada do telefone.
    telefone_verificado = otp_service.telefone_verificado_agendamento(request) == telefone

    try:
        with transaction.atomic():
            cliente = Cliente.objects.filter(telefone=telefone).first()
            cadastro_novo = cliente is None
            if cadastro_novo:
                # Form anonimo nao prova posse do e-mail: ele NUNCA entra no
                # cadastro (confirmacoes/termos iriam p/ quem digitou). Fica so
                # na inscricao, p/ o aviso desta vaga.
                cliente = Cliente.objects.create(nome=nome, telefone=telefone, ativo=True)
            # Telefone ja cadastrado e nao verificado: o e-mail digitado pode ser de
            # terceiro (receberia nome da cliente + vaga). Aviso vai pelo contato
            # do proprio cadastro (e-mail/WhatsApp) ou pela recepcao.
            # ausente = NULL, nunca '' (ck_lista_espera_email_contato_nao_vazio)
            email_contato = (email or None) if (cadastro_novo or telefone_verificado) else None

            ja_inscrito = ListaEspera.objects.filter(
                cliente=cliente,
                procedimento=procedimento,
                data_desejada=data_obj,
                notificado=False,
            ).exists()

            if not ja_inscrito:
                ListaEspera.objects.create(
                    cliente=cliente,
                    procedimento=procedimento,
                    profissional_desejado=profissional,
                    data_desejada=data_obj,
                    turno_desejado=turno,
                    email_contato=email_contato,
                )
    except IntegrityError:
        logger.warning('lista_espera_integridade', exc_info=True)
        return _erro('Não foi possível concluir sua inscrição agora. Tente novamente em instantes.')

    # Mesma pagina de sucesso p/ nova inscricao ou repetida (nao revela cadastro alheio).
    return redirect('aranha:lista_espera_sucesso')


def lista_espera_sucesso(request):
    """Confirmacao de inscricao na lista de espera."""
    return render(request, 'publico/lista_espera_sucesso.html')
