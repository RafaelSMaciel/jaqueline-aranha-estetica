"""Servico de LGPD — DSAR (export), unsubscribe, esquecimento e retencao."""
import logging
import os
import secrets
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from aranha_estetica.models import Cliente, Atendimento, AvaliacaoNPS
from aranha_estetica.utils.audit import registrar_log

logger = logging.getLogger(__name__)


def _iso(valor):
    """date/datetime -> ISO 8601 (None preservado)."""
    return valor.isoformat() if valor else None


def _dec(valor):
    """Decimal -> str (JSON sem perda de precisao)."""
    return str(valor) if valor is not None else None


class LgpdService:
    # Cliente sem atendimento ha 5 anos e anonimizado (PROJECT.md §Retencao,
    # politica de privacidade). Ajustavel por env sem deploy de codigo.
    RETENCAO_CLIENTE_INATIVO_DIAS = int(os.environ.get('LGPD_RETENCAO_CLIENTE_DIAS', 365 * 5))
    # Soft-delete vira anonimizacao apos 30 dias (PROJECT.md §Direitos).
    CARENCIA_SOFT_DELETE_DIAS = 30
    # Atendimento REALIZADO (registro de saude) e retido por 20 anos.
    RETENCAO_ATENDIMENTO_SAUDE_DIAS = 365 * 20
    # Ficha de anamnese de pedido que nao virou atendimento (rejeitado,
    # cancelado, pendente vencido, convite nunca respondido): fim da
    # finalidade (LGPD art. 15/16) -> exclusao apos N dias.
    RETENCAO_FICHA_SEM_ATENDIMENTO_DIAS = 90
    PURGA_LOG_PROGRESSO_LOTE = 100  # loga progresso a cada N anonimizacoes

    # Campos gravados pela anonimizacao (lista explicita: soft_delete() grava
    # so deletado_em/ativo e descartaria o resto — bug corrigido aqui).
    CAMPOS_ANONIMIZADOS = [
        'nome', 'cpf', 'rg', 'email', 'telefone', 'cep', 'endereco',
        'profissao', 'data_nascimento', 'foto_url', 'aceita_comunicacao',
        'token_descadastro',
        'consent_email_marketing', 'consent_email_marketing_em', 'consent_email_marketing_ip',
        'consent_whatsapp_nps', 'consent_whatsapp_nps_em', 'consent_whatsapp_nps_ip',
        'consent_whatsapp_confirmacao', 'consent_whatsapp_confirmacao_em',
        'consent_whatsapp_confirmacao_ip',
        'deletado_em', 'ativo', 'atualizado_em',
    ]

    @staticmethod
    def exportar_dados_cliente(cliente: Cliente) -> dict[str, Any]:
        """DSAR (art. 18 II/V) — exporta todos os dados do titular em dict JSON-friendly."""
        from aranha_estetica.models import (
            AceiteTermo, AnotacaoSessao, Carteira, ListaEspera, Notificacao,
            Prontuario, ProntuarioVersao, RespostaAnamnese,
        )

        atendimentos = (
            Atendimento.objects
            .filter(cliente=cliente)
            .select_related('procedimento', 'profissional')
            .order_by('-data_hora_inicio')
        )
        avaliacoes = AvaliacaoNPS.objects.filter(atendimento__cliente=cliente).order_by('-criado_em')

        prontuario = Prontuario.objects.filter(cliente=cliente).first()
        prontuario_dict = None
        if prontuario:
            prontuario_dict = {
                'alergias': prontuario.alergias,
                'contraindicacoes': prontuario.contraindicacoes,
                'historico_saude': prontuario.historico_saude,
                'medicamentos_uso': prontuario.medicamentos_uso,
                'observacoes_gerais': prontuario.observacoes_gerais,
                'respostas_extras': prontuario.respostas_extras,
                'atualizado_em': _iso(prontuario.atualizado_em),
            }
        # Historico do prontuario: estado ANTERIOR a cada edicao da equipe.
        prontuario_versoes = [
            {'registrado_em': _iso(v.criado_em), 'dados': v.dados}
            for v in ProntuarioVersao.objects.filter(prontuario__cliente=cliente).order_by('-criado_em', '-pk')
        ]

        pacotes = []
        for compra in (cliente.pacotes_comprados.select_related('pacote')
                       .prefetch_related('sessoes_realizadas').order_by('-criado_em')):
            pacotes.append({
                'pacote': compra.pacote.nome,
                'valor_pago': _dec(compra.valor_pago),
                'status': compra.status,
                'comprado_em': _iso(compra.criado_em),
                'data_expiracao': _iso(compra.data_expiracao),
                'sessoes_realizadas': len(compra.sessoes_realizadas.all()),
            })

        carteira = None
        carteira_obj = Carteira.objects.filter(cliente=cliente).first()
        if carteira_obj is not None:
            carteira = {
                'saldo': _dec(carteira_obj.saldo),
                'movimentos': [
                    {
                        'tipo': m.tipo,
                        'origem': m.origem,
                        'valor': _dec(m.valor),
                        'saldo_resultante': _dec(m.saldo_resultante),
                        'criado_em': _iso(m.criado_em),
                    }
                    for m in carteira_obj.movimentos.order_by('-criado_em')
                ],
            }

        return {
            'pessoal': {
                'nome': cliente.nome,
                'data_nascimento': _iso(cliente.data_nascimento),
                'cpf': cliente.cpf,
                'rg': cliente.rg,
                'email': cliente.email,
                'telefone': cliente.telefone,
                'cep': cliente.cep,
                'endereco': cliente.endereco,
                'profissao': cliente.profissao,
                'criado_em': _iso(cliente.criado_em),
            },
            'preferencias': {
                'aceita_comunicacao': cliente.aceita_comunicacao,
                'bloqueado_online': cliente.bloqueado_online,
            },
            'consentimentos': {
                canal: {
                    'aceito': getattr(cliente, f'consent_{canal}'),
                    'registrado_em': _iso(getattr(cliente, f'consent_{canal}_em')),
                    'ip': getattr(cliente, f'consent_{canal}_ip'),
                }
                for canal in ('email_marketing', 'whatsapp_nps', 'whatsapp_confirmacao')
            },
            'atendimentos': [
                {
                    'data': _iso(a.data_hora_inicio),
                    'procedimento': a.procedimento.nome if a.procedimento_id else None,
                    'profissional': a.profissional.nome if a.profissional_id else None,
                    'status': a.status,
                    'valor_cobrado': _dec(a.valor_cobrado),
                } for a in atendimentos
            ],
            'avaliacoes_nps': [
                {
                    'nota': a.nota,
                    'comentario': a.comentario,
                    'respondida_em': _iso(a.criado_em),
                    'autoriza_publicacao': a.autoriza_publicacao,
                }
                for a in avaliacoes
            ],
            'prontuario': prontuario_dict,
            'prontuario_versoes': prontuario_versoes,
            'anotacoes_sessao': [
                {'atendimento_data': _iso(n.atendimento.data_hora_inicio), 'texto': n.texto,
                 'criado_em': _iso(n.criado_em)}
                for n in (AnotacaoSessao.objects.filter(atendimento__cliente=cliente)
                          .select_related('atendimento').order_by('-criado_em'))
            ],
            'anamneses': [
                {'formulario': r.formulario.nome, 'respostas': r.respostas_json,
                 'criado_em': _iso(r.criado_em), 'respondida_em': _iso(r.respondida_em)}
                for r in (RespostaAnamnese.objects.filter(cliente=cliente)
                          .select_related('formulario').order_by('-criado_em'))
            ],
            'aceites_termos': [
                {'termo': a.versao_termo.titulo, 'tipo': a.versao_termo.tipo,
                 'versao': a.versao_termo.versao, 'ip': a.ip, 'aceito_em': _iso(a.criado_em),
                 'conteudo_sha256': a.conteudo_sha256}
                for a in (AceiteTermo.objects.filter(cliente=cliente)
                          .select_related('versao_termo').order_by('-criado_em'))
            ],
            'pacotes': pacotes,
            'carteira': carteira,
            'lista_espera': [
                {'procedimento': e.procedimento.nome, 'data_desejada': _iso(e.data_desejada),
                 'turno': e.turno_desejado, 'notificado': e.notificado,
                 'profissional_desejado': (
                     e.profissional_desejado.nome if e.profissional_desejado_id else None
                 ),
                 'email_contato': e.email_contato,
                 'criado_em': _iso(e.criado_em)}
                for e in (ListaEspera.objects.filter(cliente=cliente)
                          .select_related('procedimento', 'profissional_desejado').order_by('-criado_em'))
            ],
            'notificacoes': [
                {'tipo': n.tipo, 'canal': n.canal, 'status': n.status,
                 'enviado_em': _iso(n.enviado_em), 'criado_em': _iso(n.criado_em)}
                for n in Notificacao.objects.filter(atendimento__cliente=cliente).order_by('-criado_em')
            ],
        }

    @staticmethod
    def cliente_por_token_descadastro(token: str) -> Cliente | None:
        """Cliente ativo dono do token de descadastro (None se invalido)."""
        if not token:
            return None
        return Cliente.objects.filter(token_descadastro=token).first()

    @staticmethod
    def unsubscribe_por_token(token: str) -> Cliente | None:
        """Opt-out de marketing e pesquisas via token publico (link do e-mail).

        Zera consent legado (aceita_comunicacao) e os granulares de marketing
        (e-mail) e pesquisa (NPS). O lembrete do agendamento
        (consent_whatsapp_confirmacao) e aviso transacional e continua — a
        pagina de descadastro informa isso ao cliente.
        """
        cliente = LgpdService.cliente_por_token_descadastro(token)
        if cliente is None:
            return None
        cliente.aceita_comunicacao = False
        cliente.consent_email_marketing = False
        cliente.consent_whatsapp_nps = False
        cliente.save(update_fields=[
            'aceita_comunicacao', 'consent_email_marketing',
            'consent_whatsapp_nps', 'atualizado_em',
        ])
        logger.info('Cliente %s opt-out via token.', cliente.pk)
        return cliente

    @classmethod
    @transaction.atomic
    def esquecer_cliente(cls, cliente: Cliente, usuario=None, request=None) -> None:
        """Direito ao esquecimento: anonimiza PII do titular e faz soft-delete.

        Mantem atendimentos/aceites/prontuario e as fichas de anamnese de
        atendimentos REALIZADOS (retencao clinica) ligados a um titular nao
        identificavel. Fichas de pedidos nao realizados sao apagadas.
        Depoimento (NPS) sai do site e perde o texto livre (a nota fica p/
        estatistica). LogAuditoria continua (trilha legal — autor, tabela,
        registro, IP), mas o nome do titular no texto da acao vira o
        pseudonimo; o mesmo no historico do Django admin (django_admin_log).
        """
        from aranha_estetica.models import (
            Carteira, CodigoOtp, CompraPacote, ListaEspera, LogAuditoria, Notificacao,
            Prontuario, RespostaAnamnese,
        )

        cliente_pk = cliente.pk
        telefone_original = cliente.telefone
        email_original = cliente.email
        nome_original = cliente.nome or ''
        pseudonimo = f'[ANONIMIZADO-{cliente_pk}]'

        cliente.nome = pseudonimo
        cliente.cpf = None
        cliente.rg = None
        cliente.email = None
        cliente.telefone = None
        cliente.cep = None
        cliente.endereco = None
        cliente.profissao = None
        cliente.data_nascimento = None
        cliente.foto_url = ''
        cliente.aceita_comunicacao = False
        # Token novo: links de descadastro antigos deixam de identificar o titular.
        cliente.token_descadastro = secrets.token_urlsafe(32)
        for canal in ('email_marketing', 'whatsapp_nps', 'whatsapp_confirmacao'):
            setattr(cliente, f'consent_{canal}', False)
            setattr(cliente, f'consent_{canal}_em', None)
            setattr(cliente, f'consent_{canal}_ip', None)
        if cliente.deletado_em is None:
            cliente.deletado_em = timezone.now()
        cliente.ativo = False
        cliente.save(update_fields=cls.CAMPOS_ANONIMIZADOS)

        # Rastros com PII fora do cadastro: OTPs (telefone/pseudo-email/IP),
        # lista de espera e texto das notificacoes (nome do cliente).
        otp_q = Q()
        if telefone_original:
            otp_q |= Q(telefone=telefone_original)
            otp_q |= Q(email=CodigoOtp.email_para_telefone(telefone_original))
        if email_original:
            otp_q |= Q(email__iexact=email_original)
        if otp_q:
            CodigoOtp.objects.filter(otp_q).delete()
        espera_ids = list(
            ListaEspera.objects.filter(cliente_id=cliente_pk).values_list('pk', flat=True)
        )
        ListaEspera.objects.filter(pk__in=espera_ids).delete()
        Notificacao.objects.filter(atendimento__cliente_id=cliente_pk).update(mensagem='')
        # Dado de saude sem atendimento realizado: fim da finalidade -> apaga.
        RespostaAnamnese.objects.filter(cliente_id=cliente_pk).exclude(
            atendimento__status=Atendimento.STATUS_REALIZADO,
        ).delete()
        # Depoimento: sai do site (autorizacao revogada) e o texto livre some;
        # a nota fica (estatistica de NPS sem titular identificavel).
        AvaliacaoNPS.objects.filter(atendimento__cliente_id=cliente_pk).update(
            autoriza_publicacao=False, aprovado_publicacao=False, comentario='',
        )

        # Trilha de auditoria: registros do titular (cliente/prontuario/
        # atendimento/pacote) guardam autor, data, tabela e id; so o nome sai do texto.
        prontuario_ids = list(
            Prontuario.objects.filter(cliente_id=cliente_pk).values_list('pk', flat=True)
        )
        atendimento_ids = list(
            Atendimento.objects.filter(cliente_id=cliente_pk).values_list('pk', flat=True)
        )
        compra_ids = list(
            CompraPacote.objects.filter(cliente_id=cliente_pk).values_list('pk', flat=True)
        )
        if len(nome_original.strip()) >= 3 and not nome_original.startswith('[ANONIMIZADO-'):
            from django.db.models import Value
            from django.db.models.functions import Replace

            do_titular = (
                Q(tabela='cliente', registro_id=cliente_pk)
                | Q(tabela='prontuario', registro_id__in=[cliente_pk, *prontuario_ids])
                | Q(tabela='atendimento', registro_id__in=atendimento_ids)
                | Q(tabela='compra_pacote', registro_id__in=compra_ids)
            )
            LogAuditoria.objects.filter(do_titular, acao__contains=nome_original).update(
                acao=Replace('acao', Value(nome_original), Value(pseudonimo)),
            )
        # Nome ANTERIOR a uma edicao do cadastro ('Editou cliente: <nome antigo>').
        LogAuditoria.objects.filter(
            tabela='cliente', registro_id=cliente_pk, acao__startswith='Editou cliente:',
        ).update(acao=f'Editou cliente: {pseudonimo}')
        # Telefone/CPF/e-mail (mascarados) que a migration 0034 guardou em detalhes.
        LogAuditoria.objects.filter(
            tabela='cliente', registro_id=cliente_pk, acao__startswith='migration 0034',
        ).update(detalhes=None)
        # Django admin: object_repr = str(obj), que traz o nome do cliente.
        cls._pseudonimizar_admin_log(pseudonimo, [
            (Cliente, [cliente_pk]),
            (Atendimento, atendimento_ids),
            (CompraPacote, compra_ids),
            (ListaEspera, espera_ids),
            (AvaliacaoNPS, AvaliacaoNPS.objects.filter(
                atendimento__cliente_id=cliente_pk).values_list('pk', flat=True)),
            (Notificacao, Notificacao.objects.filter(
                atendimento__cliente_id=cliente_pk).values_list('pk', flat=True)),
            (Carteira, Carteira.objects.filter(cliente_id=cliente_pk).values_list('pk', flat=True)),
        ])

        # Trilha de auditoria LGPD (sem PII no registro).
        registrar_log(
            usuario,
            'Cliente anonimizado (direito ao esquecimento / retencao LGPD)',
            'cliente',
            cliente_pk,
            request=request,
        )
        logger.info('Cliente %s anonimizado (direito ao esquecimento).', cliente_pk)

    @staticmethod
    def _pseudonimizar_admin_log(pseudonimo: str, objetos) -> int:
        """django_admin_log: object_repr (str(obj) = nome do cliente) vira o pseudonimo.

        objetos: [(Model, ids)]. change_message guarda so nomes de campos
        (sem valores) e fica. Retorna quantas entradas foram reescritas.
        """
        from django.contrib.admin.models import LogEntry
        from django.contrib.contenttypes.models import ContentType

        total = 0
        for model, ids in objetos:
            ids = [str(pk) for pk in ids]
            if not ids:
                continue
            total += LogEntry.objects.filter(
                content_type=ContentType.objects.get_for_model(model, for_concrete_model=False),
                object_id__in=ids,
            ).update(object_repr=pseudonimo)
        return total

    @classmethod
    def candidatos_purga(cls):
        """Clientes elegiveis a anonimizacao automatica.

        - ativo criado ha mais de N anos sem atendimento nesse periodo, OU
          soft-deletado ha mais de 30 dias;
        - nunca quem ja foi anonimizado;
        - nunca quem tem registro com retencao legal: prontuario com conteudo
          clinico, pacote comprado (fiscal) ou atendimento REALIZADO nos
          ultimos 20 anos (registro de saude).
        Aceite de termo NAO retem a identidade: anonimizar e UPDATE, a linha de
        AceiteTermo (prova do consentimento) continua ligada ao mesmo id. Todo
        booking online grava o aceite LGPD — reter por ele desligaria a purga.
        """
        from aranha_estetica.models import Prontuario

        agora = timezone.now()
        limite = agora - timedelta(days=cls.RETENCAO_CLIENTE_INATIVO_DIAS)
        limite_soft = agora - timedelta(days=cls.CARENCIA_SOFT_DELETE_DIAS)
        limite_saude = agora - timedelta(days=cls.RETENCAO_ATENDIMENTO_SAUDE_DIAS)

        base = Cliente.all_objects.filter(
            Q(deletado_em__isnull=True, criado_em__lt=limite)
            | Q(deletado_em__lt=limite_soft)
        ).exclude(nome__startswith='[ANONIMIZADO-')

        com_atendimento_recente = Atendimento.objects.filter(
            data_hora_inicio__gte=limite,
        ).values('cliente_id')
        com_saude_retida = Atendimento.objects.filter(
            status=Atendimento.STATUS_REALIZADO,
            data_hora_inicio__gte=limite_saude,
        ).values('cliente_id')
        # Prontuario vazio (GET antigo do painel fazia get_or_create) nao retem.
        com_prontuario = Prontuario.objects.filter(
            Prontuario.Q_COM_CONTEUDO, cliente_id=OuterRef('pk'),
        )

        return (
            base
            .exclude(pk__in=com_atendimento_recente)
            .exclude(pk__in=com_saude_retida)
            .exclude(Exists(com_prontuario))
            .exclude(pacotes_comprados__isnull=False)
            .distinct()
        )

    @classmethod
    def fichas_sem_atendimento_para_purga(cls, dias: int | None = None):
        """Fichas de anamnese de pedido nao realizado, criadas ha mais de N dias.

        Criterio e o status do atendimento DA PROPRIA ficha (nunca "cliente
        tem algum REALIZADO", que protegeria para sempre as fichas de quem e
        recorrente): CANCELADO; PENDENTE com horario ja passado; ficha sem
        atendimento nunca respondida; ou convite vazio (sem respostas) cujo
        horario ja passou (link enviado pela recepcao e ignorado).
        Ficha RESPONDIDA de FALTOU/REAGENDADO fica (decisao da clinica).
        """
        from aranha_estetica.models import RespostaAnamnese

        agora = timezone.now()
        dias = cls.RETENCAO_FICHA_SEM_ATENDIMENTO_DIAS if dias is None else dias
        return RespostaAnamnese.objects.filter(
            criado_em__lt=agora - timedelta(days=dias),
        ).filter(
            Q(atendimento__status=Atendimento.STATUS_CANCELADO)
            | Q(atendimento__status=Atendimento.STATUS_PENDENTE, atendimento__data_hora_inicio__lt=agora)
            | Q(atendimento__isnull=True, respondida_em__isnull=True)
            # convite vazio: ficha legada (booking antigo) tem respostas e
            # respondida_em nulo — essa NUNCA entra aqui
            | Q(respondida_em__isnull=True, respostas_json={}, atendimento__data_hora_inicio__lt=agora)
        )

    @classmethod
    def purgar_fichas_sem_atendimento(cls, dias: int | None = None) -> int:
        """Apaga as fichas de fichas_sem_atendimento_para_purga (retencao LGPD)."""
        with transaction.atomic():
            qtd, _por_modelo = cls.fichas_sem_atendimento_para_purga(dias).delete()
            if qtd:
                registrar_log(
                    None, 'Purga de fichas de pedido nao realizado', 'resposta_anamnese', None,
                    detalhes={'qtd': qtd},
                )
        logger.info('purgar_fichas_sem_atendimento: %s ficha(s) apagada(s).', qtd)
        return qtd

    @staticmethod
    def purgar_lista_espera_vencida() -> int:
        """Apaga inscricoes da lista de espera cuja data desejada ja passou.

        Fim da finalidade (LGPD art. 15/16): o aviso de vaga nao pode mais
        acontecer e a inscricao guarda contato (email_contato) de quem pediu.
        """
        from aranha_estetica.models import ListaEspera
        from aranha_estetica.utils.datas import hoje

        with transaction.atomic():
            qtd, _por_modelo = ListaEspera.objects.filter(data_desejada__lt=hoje()).delete()
            if qtd:
                registrar_log(
                    None, 'Purga da lista de espera vencida', 'lista_espera', None,
                    detalhes={'qtd': qtd},
                )
        logger.info('purgar_lista_espera_vencida: %s inscricao(oes) apagada(s).', qtd)
        return qtd

    @classmethod
    def purgar_inativos(cls) -> int:
        """Anonimiza os candidatos de retencao (ver candidatos_purga).

        Cada esquecer_cliente roda em sua propria transacao: um aborto no meio
        deixa os ja anonimizados comitados e o resto e retomado no proximo run.
        """
        count = 0
        for cliente in cls.candidatos_purga().iterator():
            cls.esquecer_cliente(cliente)
            count += 1
            if count % cls.PURGA_LOG_PROGRESSO_LOTE == 0:
                logger.info('purgar_inativos: %s clientes anonimizados ate agora.', count)
        logger.info('purgar_inativos: concluido, %s clientes anonimizados.', count)
        return count
