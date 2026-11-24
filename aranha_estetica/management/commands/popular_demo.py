"""Popula o AMBIENTE DE TESTES com uma operacao ficticia ENXUTA, para demonstracao.

Depois do `seed` (catalogo real, a Jaqueline, agenda, termo LGPD, ficha de anamnese): 3 clientes e
2 profissionais (a Jaqueline e a esteticista ficticia Bianca), com ~6 semanas de historico e os
proximos dias — o bastante para toda tela do painel ter o que mostrar (agenda, calendario,
retornos, clientes, prontuario, notificacoes, promocoes, anamneses, pacotes, financeiro,
comissoes, NPS, bloqueios, lista de espera, termos, portal da profissional) sem poluir o
ambiente. Ate 2026-11-24 eram 24 clientes e ~200 atendimentos sorteados; o dono pediu o ambiente
enxuto, com os valores simulados.

Tudo passa pelos mesmos servicos que o sistema usa (agenda.reservar + transicoes da FSM): comissao,
debito de pacote, cashback de indicacao, faltas e retornos saem coerentes. O roteiro e fixo (sem
sorteio): rodar em outro banco da a mesma operacao, so com as datas relativas a hoje.

    python manage.py popular_demo

- Recusado em producao (mesma regra do `seed --demo`); roda no pre-deploy do environment dev.
- Idempotente: se a primeira cliente da demonstracao ja existe, nao faz nada (varias tabelas
  sao append-only no Postgres: nao ha "refazer" — p/ recomecar, banco novo).
- Nunca derruba o pre-deploy: erro vira aviso no stderr e a transacao inteira e desfeita.
- Nada sai para fora: e-mails @example.com, celulares ficticios (11 90000-01xx) e nenhum servico
  que enfileira e-mail ou WhatsApp e chamado.
- O horario das 10:00 fica sempre livre nos proximos dias (roteiro da apresentacao).
- Login da 2a profissional (portal /profissional/): DEMO_PROFISSIONAL_EMAIL/DEMO_PROFISSIONAL_SENHA
  na env; sem a senha a conta e criada sem senha utilizavel.
"""
import os
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from aranha_estetica.ator import Ator, atuando
from aranha_estetica.management.commands.seed import _em_producao
from aranha_estetica.models import (
    AgendaExcecao, AgendaHorario, AnotacaoSessao, Atendimento, Cliente, CompraPacote,
    FormularioAnamnese, Habilitacao, ItemPacote, Pacote, Preco, Procedimento,
    Profissional, Promocao, Prontuario, ProntuarioVersao, RegraComissao, RespostaAnamnese,
    Usuario, VersaoTermo,
)
from aranha_estetica.models.notificacoes import (
    CanalNotificacao, Notificacao, RespostaNotificacao, StatusNotificacao, TipoNotificacao,
)
from aranha_estetica.models.termos import AceiteTermo

# (nome, celular, nascimento, cadastrada ha quantos dias) — celulares ficticios 11 90000-01xx
CLIENTES = [
    ('Ana Beatriz Moura', '11900000101', date(1991, 3, 14), 50),
    ('Camila Ferreira Santos', '11900000102', date(1988, 7, 2), 42),
    ('Juliana Rocha Lima', '11900000103', date(1995, 11, 25), 15),   # "nova do mes" no painel
]
ANA, CAMILA, JULIANA = 0, 1, 2
INDICACOES = {CAMILA: ANA}            # indicada -> quem indicou (cashback no 1o atendimento realizado)

PROF2 = {
    'nome': 'Bianca Moraes',
    'especialidade': 'Esteticista · Limpeza de pele, drenagem e massagens',
    'bio': 'Esteticista com foco em cuidados corporais e faciais não invasivos.',
}
PROF2_PROCEDIMENTOS = ('Limpeza de Pele', 'Drenagem Linfática Corporal', 'Drenagem Linfática Facial',
                       'Endermoterapia', 'Ventosaterapia', 'Massagem Relaxante')
PROF2_EMAIL_PADRAO = 'bianca@demo.jaquelinearanha.test'
REGRA_JAQUELINE = Decimal('30.00')    # % de comissao (so se ainda nao ha regra dela)
REGRA_PROF2 = Decimal('40.00')

BOTOX = ('Botox 1 região', 'Botox 2 regiões', 'Botox 3 regiões')
RETORNO_DIAS = (10, 20)               # janela do retorno do botox (ajuste de dose)

SEG_A_SEX = (0, 1, 2, 3, 4)           # expediente da Jaqueline (seed)
TER_A_SEX = (1, 2, 3, 4)              # Bianca (ela atende tambem no sabado, mas so ate as 15h)

PACOTES = [
    # (nome, procedimento, sessoes, preco_total, descricao)
    ('Drenagem Linfática — 10 sessões', 'Drenagem Linfática Corporal', 10, '1000.00',
     'Dez sessões de drenagem corporal com valor especial.'),
    ('Enzimas Gordura Localizada — 8 sessões', 'Enzimas para Gordura Localizada', 8, '840.00',
     'Protocolo de oito aplicações de enzimas.'),
]
PACOTE_VENDIDO = ('Drenagem Linfática Corporal', ANA, 44)   # (procedimento, cliente, vendido ha N dias)

# Historico: (dias atras, hora, cliente, profissional 'J'/'B', procedimento, desfecho).
# O dia recua ate cair no expediente da profissional. Desfecho: realizado | faltou | cancelado |
# em_aberto (ja passou e ninguem marcou: o aviso "sem desfecho" da visao geral).
HISTORICO = [
    (42, time(16, 30), ANA, 'B', 'Drenagem Linfática Corporal', 'realizado'),      # sessoes do pacote
    (40, time(14, 0), CAMILA, 'J', 'Preenchimento Labial', 'realizado'),
    (35, time(16, 30), ANA, 'B', 'Drenagem Linfática Corporal', 'realizado'),
    (30, time(9, 0), ANA, 'J', 'Botox 1 região', 'realizado'),                     # retorno feito (-16)
    (28, time(16, 30), ANA, 'B', 'Drenagem Linfática Corporal', 'realizado'),
    (26, time(11, 0), CAMILA, 'B', 'Limpeza de Pele', 'realizado'),
    (21, time(16, 30), ANA, 'B', 'Drenagem Linfática Corporal', 'realizado'),
    (20, time(15, 30), CAMILA, 'J', 'Skinbooster', 'faltou'),
    (14, time(16, 30), ANA, 'B', 'Drenagem Linfática Corporal', 'realizado'),
    (13, time(9, 0), JULIANA, 'J', 'Botox 1 região', 'realizado'),                 # retorno marcado (+4)
    (12, time(14, 0), CAMILA, 'J', 'Botox 2 regiões', 'realizado'),                # retorno a marcar
    (9, time(11, 0), ANA, 'J', 'Bioestimulador de Colágeno', 'realizado'),
    (7, time(16, 30), ANA, 'B', 'Drenagem Linfática Corporal', 'realizado'),
    (6, time(13, 30), JULIANA, 'B', 'Limpeza de Pele', 'realizado'),
    (5, time(15, 30), CAMILA, 'J', 'Microagulhamento', 'realizado'),
    (3, time(15, 0), JULIANA, 'B', 'Massagem Relaxante', 'cancelado'),
    (2, time(14, 0), JULIANA, 'J', 'Skinbooster', 'realizado'),
    (1, time(15, 30), CAMILA, 'J', 'Enzimas para Gordura Localizada', 'em_aberto'),
]
RETORNO_FEITO = (ANA, 'Botox 1 região', 16)        # (cliente, procedimento, ha quantos dias)
RETORNO_MARCADO = (JULIANA, 'Botox 1 região', 4)   # (cliente, procedimento, daqui a quantos dias)

# Proximos dias: (daqui a N dias, hora, cliente, profissional, procedimento, status). O dia avanca
# ate cair no expediente; nada as 10:00 (o roteiro da apresentacao agenda nesse horario).
PROXIMOS = [
    (0, time(15, 0), CAMILA, 'B', 'Limpeza de Pele', 'confirmado'),
    (1, time(14, 0), ANA, 'J', 'Preenchimento de Malar', 'confirmado'),
    (2, time(16, 30), ANA, 'B', 'Drenagem Linfática Corporal', 'agendado'),        # 7a sessao do pacote
    (3, time(11, 0), CAMILA, 'J', 'PRP (Fibrina)', 'agendado'),
    (6, time(14, 0), JULIANA, 'J', 'Bioestimulador de Colágeno', 'pendente'),      # pedido do site
]

# Avaliacoes (NPS): posicao no HISTORICO -> (nota, comentario, publica como depoimento no site)
NPS = {
    1: (10, 'A Jaqueline é muito cuidadosa e explica tudo antes. Amei o resultado.', True),
    3: (10, 'Atendimento impecável, me senti muito acolhida. Resultado natural!', True),
    4: (9, 'Saí renovada da drenagem, a Bianca tem mãos de fada.', True),
    5: (8, 'Gostei, só achei que atrasou um pouco.', False),
    9: (10, '', False),
    11: (9, 'Ambiente lindo e tranquilo. Já marquei a próxima sessão.', False),
    13: (10, 'Resultado superou minhas expectativas, muito natural.', False),
}

# Ficha de anamnese de cada cliente (respondida no 1o atendimento com a Jaqueline)
FICHAS = {
    ANA: {'gestante_ou_amamentando': False, 'tem_alergia': False, 'uso_de_acidos': False,
          'fez_procedimento_recente': False},
    CAMILA: {'gestante_ou_amamentando': False, 'tem_alergia': True, 'alergias': 'Lidocaína',
             'uso_de_acidos': False, 'fez_procedimento_recente': False},
    JULIANA: {'gestante_ou_amamentando': False, 'tem_alergia': False, 'uso_de_acidos': True,
              'fez_procedimento_recente': True, 'procedimentos_recentes': 'Peeling químico há 2 meses'},
}
ANOTACOES = [
    'Aplicação conforme planejado. Orientada a não deitar nas próximas 4 horas.',
    'Pele reagiu bem, leve eritema esperado. Retorno conforme protocolo.',
    'Cliente relatou sensibilidade moderada; aplicada anestesia tópica.',
]


def _aware(dia, hora):
    return timezone.make_aware(datetime.combine(dia, hora), timezone.get_current_timezone())


def _email(nome):
    import unicodedata
    base = unicodedata.normalize('NFKD', nome).encode('ascii', 'ignore').decode().lower().split()
    return f'{base[0]}.{base[-1]}@example.com'


class Command(BaseCommand):
    help = 'Popula o ambiente de testes com uma operação fictícia enxuta (idempotente; nunca em produção).'

    def handle(self, *args, **options):
        if _em_producao():
            self.stderr.write('popular_demo: recusado em produção.')
            return
        if Cliente.all_objects.filter(telefone=CLIENTES[0][1]).exists():
            self.stdout.write('popular_demo: demonstração já populada — nada a fazer.')
            return
        try:
            admin = Usuario.objects.filter(papel=Usuario.PAPEL_ADMIN, ativo=True).order_by('pk').first()
            with atuando(Ator(usuario=admin, origem='comando:popular_demo')), transaction.atomic():
                resumo = Populador(admin, self.stdout).executar()
        except Exception as exc:  # noqa: BLE001 — o pre-deploy do dev nao pode cair por isso
            self.stderr.write(f'popular_demo: falhou e nada foi gravado ({exc.__class__.__name__}: {exc}).')
            if options.get('verbosity', 1) > 1:
                raise
            return
        self.stdout.write(self.style.SUCCESS(f'popular_demo: {resumo}'))


class Populador:
    def __init__(self, admin, stdout=sys.stdout):
        self.admin = admin
        self.out = stdout
        self.hoje = timezone.localdate()
        self.agora = timezone.now()

    # ── orquestracao ────────────────────────────────────────────────────

    def executar(self) -> str:
        self.jaqueline = Profissional.objects.get(nome='Jaqueline Aranha')
        self.procs = {p.nome: p for p in Procedimento.objects.filter(ativo=True)}
        self._retorno_do_botox()
        self.bianca = self._segunda_profissional()
        self.profs = {'J': self.jaqueline, 'B': self.bianca}
        self.clientes = self._clientes()
        self.termo_labial = self._termo_procedimento('Preenchimento Labial')
        self._pacotes()
        self._bloqueios()
        self._promocoes()
        passados = self._historico()
        futuros = self._proximos_dias()
        self._nps(passados)
        self._fichas_e_prontuarios(passados)
        self._notificacoes(passados, futuros)
        self._comissoes()
        self._lista_espera()
        return (f'{len(self.clientes)} clientes, 2 profissionais, {len(passados)} atendimentos no histórico e '
                f'{len(futuros)} nos próximos dias.')

    # ── cadastros ───────────────────────────────────────────────────────

    def _retorno_do_botox(self):
        for nome in BOTOX:
            proc = self.procs.get(nome)
            if proc and not proc.exige_retorno:
                proc.exige_retorno = True
                proc.retorno_minimo_dias, proc.retorno_maximo_dias = RETORNO_DIAS
                proc.save(update_fields=['exige_retorno', 'retorno_minimo_dias', 'retorno_maximo_dias'])

    def _segunda_profissional(self):
        # comissao da Jaqueline: o `seed` so cria a regra com --demo
        if not RegraComissao.objects.filter(profissional=self.jaqueline).exists():
            RegraComissao.objects.create(profissional=self.jaqueline, percentual=REGRA_JAQUELINE, ativo=True)
        prof, _ = Profissional.objects.get_or_create(nome=PROF2['nome'], defaults={
            'especialidade': PROF2['especialidade'], 'bio': PROF2['bio'], 'ativo': True})
        for nome in PROF2_PROCEDIMENTOS:
            if nome in self.procs:
                Habilitacao.objects.get_or_create(profissional=prof, procedimento=self.procs[nome])
        if not AgendaHorario.objects.filter(profissional=prof).exists():
            for dia in range(2, 7):   # terca a sabado
                fim = time(15, 0) if dia == 6 else time(18, 0)
                AgendaHorario.objects.create(profissional=prof, dia_semana=dia, hora_inicio=time(9, 0), hora_fim=fim)
        if not RegraComissao.objects.filter(profissional=prof).exists():
            RegraComissao.objects.create(profissional=prof, percentual=REGRA_PROF2, ativo=True)
        email = (os.environ.get('DEMO_PROFISSIONAL_EMAIL') or PROF2_EMAIL_PADRAO).strip().lower()
        if not Usuario.objects.filter(email=email).exists():
            Usuario.objects.create_user(
                email, password=os.environ.get('DEMO_PROFISSIONAL_SENHA') or None,
                nome=PROF2['nome'], papel=Usuario.PAPEL_PROFISSIONAL, profissional=prof)
        return prof

    def _clientes(self):
        from aranha_estetica.services import consentimentos as cons

        lgpd = VersaoTermo.lgpd_vigente()
        # os avisos sao escolhidos em Meus agendamentos > Minhas preferencias (origem PORTAL)
        origem = cons.Consentimento.ORIGEM_PORTAL
        autorizacoes = {ANA: (cons.LEMBRETE_WHATSAPP, cons.PESQUISA_WHATSAPP, cons.MARKETING_EMAIL),
                        CAMILA: (cons.LEMBRETE_WHATSAPP, cons.PESQUISA_WHATSAPP),
                        JULIANA: (cons.LEMBRETE_WHATSAPP,)}
        clientes = []
        for i, (nome, telefone, nascimento, dias) in enumerate(CLIENTES):
            cliente = Cliente.objects.create(nome=nome, telefone=telefone, email=_email(nome),
                                             data_nascimento=nascimento)
            criado = _aware(self.hoje - timedelta(days=dias), time(10))
            Cliente.objects.filter(pk=cliente.pk).update(criado_em=criado)
            clientes.append(cliente)
            if lgpd:
                AceiteTermo.registrar(cliente, lgpd)
            for par in autorizacoes[i]:
                cons.registrar(cliente, *par, True, origem=origem, registrado_em=criado)
        for indicada, quem in INDICACOES.items():
            Cliente.objects.filter(pk=clientes[indicada].pk).update(indicado_por=clientes[quem])
            clientes[indicada].refresh_from_db()
        self.out.write(f'popular_demo: {len(clientes)} clientes.')
        return clientes

    def _termo_procedimento(self, nome_proc):
        proc = self.procs.get(nome_proc)
        if proc is None:
            return None
        termo = VersaoTermo.objects.filter(tipo='PROCEDIMENTO', procedimento=proc, ativa=True).first()
        if termo is None:
            termo = VersaoTermo.objects.create(
                tipo='PROCEDIMENTO', procedimento=proc, versao='1.0', ativa=True,
                vigente_desde=self.hoje - timedelta(days=60),
                titulo=f'Termo de consentimento — {nome_proc}',
                conteudo=(f'Declaro que fui informada sobre o procedimento de {nome_proc.lower()}, seus '
                          'cuidados, possíveis efeitos (inchaço, hematomas e sensibilidade temporários) e '
                          'contraindicações, e que tive minhas dúvidas esclarecidas pela profissional.'),
            )
        return termo

    def _pacotes(self):
        """Dois pacotes no catalogo e UMA venda (a Ana, com as sessoes semanais do historico)."""
        nome_proc_vendido, idx, dias = PACOTE_VENDIDO
        for nome, nome_proc, sessoes, total, descricao in PACOTES:
            proc = self.procs.get(nome_proc)
            if proc is None:
                continue
            pacote, novo = Pacote.objects.get_or_create(nome=nome, defaults={
                'descricao': descricao, 'preco_total': Decimal(total)})
            if novo:
                ItemPacote.objects.create(pacote=pacote, procedimento=proc, quantidade_sessoes=sessoes)
            if nome_proc == nome_proc_vendido:
                compra = CompraPacote.vender(pacote, self.clientes[idx], pacote.preco_total)
                vendido = _aware(self.hoje - timedelta(days=dias), time(9, 30))
                CompraPacote.objects.filter(pk=compra.pk).update(criado_em=vendido)

    def _bloqueios(self):
        self.dia_curso = self._avancar(self.hoje + timedelta(days=8), SEG_A_SEX, self.jaqueline)
        bloqueio = AgendaExcecao(
            tipo=AgendaExcecao.TIPO_BLOQUEIO, profissional=self.jaqueline,
            data_hora_inicio=_aware(self.dia_curso, time(13, 0)), data_hora_fim=_aware(self.dia_curso, time(18, 0)),
            motivo='Curso de atualização em harmonização facial', usuario_autor=self.admin)
        bloqueio.full_clean()
        bloqueio.save()
        self.folga_bianca = self._avancar(self.hoje + timedelta(days=9), (1, 2, 3, 4), self.bianca)
        folga = AgendaExcecao(tipo=AgendaExcecao.TIPO_FOLGA, profissional=self.bianca,
                              data_inicio=self.folga_bianca, data_fim=self.folga_bianca,
                              motivo='Folga', usuario_autor=self.admin)
        folga.full_clean()
        folga.save()

    def _promocoes(self):
        limpeza = self.procs.get('Limpeza de Pele')
        if limpeza and not Promocao.objects.filter(procedimento=limpeza, ativa=True).exists():
            Promocao.objects.create(procedimento=limpeza, nome='Mês da pele — Limpeza de Pele',
                                    desconto_percentual=Decimal('20'), ativa=True,
                                    data_inicio=self.hoje.replace(day=1), data_fim=self.hoje + timedelta(days=21))
        drenagem = self.procs.get('Drenagem Linfática Corporal')
        if drenagem and not Promocao.objects.filter(nome__startswith='Dia das Mães').exists():
            ano = self.hoje.year
            Promocao.objects.create(procedimento=drenagem, nome='Dia das Mães — Drenagem',
                                    preco_promocional=Decimal('99.00'), ativa=False,
                                    data_inicio=date(ano, 5, 1), data_fim=date(ano, 5, 15))

    # ── agenda ──────────────────────────────────────────────────────────

    def _feriado(self, dia, prof):
        qs = AgendaExcecao.objects.filter(tipo=AgendaExcecao.TIPO_FOLGA, ativo=True,
                                          data_inicio__lte=dia, data_fim__gte=dia)
        return qs.filter(profissional__isnull=True).exists() or qs.filter(profissional=prof).exists()

    def _avancar(self, dia, dias_semana, prof):
        """O proprio dia ou o proximo em que a profissional atende (expediente, sem feriado/folga)."""
        while dia.weekday() not in dias_semana or self._feriado(dia, prof):
            dia += timedelta(days=1)
        return dia

    def _recuar(self, dia, dias_semana, prof):
        """O proprio dia ou o anterior em que a profissional atendeu."""
        while dia.weekday() not in dias_semana or self._feriado(dia, prof):
            dia -= timedelta(days=1)
        return dia

    def _expediente(self, sigla):
        return SEG_A_SEX if sigla == 'J' else TER_A_SEX

    def _preco_base(self, proc):
        preco = (Preco.objects.filter(procedimento=proc, profissional__isnull=True)
                 .order_by('-vigente_desde').first())
        return preco.valor if preco else None

    def _reservar(self, cliente, prof, proc, inicio, status, **extra):
        from aranha_estetica.services import agenda

        fim = inicio + timedelta(minutes=proc.duracao_minutos)
        if Atendimento.objects.conflito_com(prof, inicio, fim).exists():
            return None
        if inicio < self.agora and 'preco' not in extra:
            valor = self._preco_base(proc)
            if valor is None:
                return None
            extra['preco'] = {'valor_cobrado': valor, 'valor_original': None, 'promocao': None,
                              'descricao_preco': ''}
        try:
            with transaction.atomic():
                return agenda.reservar(cliente=cliente, profissional=prof, procedimento=proc,
                                       inicio=inicio, status=status, **extra)
        except Exception:  # noqa: BLE001 — horario disputado: so pula
            return None

    def _historico(self):
        from aranha_estetica.services import atendimento as svc

        planos = []
        for posicao, (dias, hora, idx, sigla, nome_proc, desfecho) in enumerate(HISTORICO):
            prof = self.profs[sigla]
            dia = self._recuar(self.hoje - timedelta(days=dias), self._expediente(sigla), prof)
            if nome_proc in self.procs:
                planos.append((_aware(dia, hora), posicao, self.clientes[idx], prof, self.procs[nome_proc], desfecho))
        planos.sort(key=lambda p: p[0])

        feitos = []
        self.por_posicao = {}   # posicao no HISTORICO -> atendimento (p/ as avaliacoes)
        for inicio, posicao, cliente, prof, proc, desfecho in planos:
            if inicio >= self.agora - timedelta(hours=1):
                continue
            at = self._reservar(cliente, prof, proc, inicio, Atendimento.STATUS_AGENDADO)
            if at is None:
                continue
            if desfecho == 'faltou':
                at.marcar_falta(by_user=self.admin)
            elif desfecho == 'cancelado':
                at.cancelar(motivo='Cliente pediu para desmarcar', by_user=self.admin)
            elif desfecho == 'realizado':
                if self.termo_labial and proc == self.termo_labial.procedimento:
                    AceiteTermo.registrar(cliente, self.termo_labial, None, at)
                svc.marcar_realizado(at, por=self.admin, aceitar_sem_termo=True)
            feitos.append(at)
            self.por_posicao[posicao] = at
        self._retorno_feito(feitos)
        return feitos

    def _origem_do_retorno(self, atendimentos, idx, nome_proc):
        return next((a for a in atendimentos if a.cliente_id == self.clientes[idx].pk
                     and a.procedimento.nome == nome_proc and a.status == Atendimento.STATUS_REALIZADO
                     and not a.eh_retorno), None)

    def _retorno_feito(self, feitos):
        """O retorno sem custo do botox da Ana, ja realizado."""
        from aranha_estetica.services import atendimento as svc
        from aranha_estetica.services import retorno

        idx, nome_proc, dias = RETORNO_FEITO
        origem = self._origem_do_retorno(feitos, idx, nome_proc)
        if origem is None:
            return
        dia = self._recuar(self.hoje - timedelta(days=dias), SEG_A_SEX, origem.profissional)
        volta = self._reservar(origem.cliente, origem.profissional, origem.procedimento, _aware(dia, time(11, 0)),
                               Atendimento.STATUS_AGENDADO, preco=dict(retorno.PRECO_RETORNO),
                               eh_retorno=True, atendimento_origem=origem)
        if volta is not None:
            svc.marcar_realizado(volta, por=self.admin, aceitar_sem_termo=True)
            feitos.append(volta)

    def _proximos_dias(self):
        from aranha_estetica.services import retorno

        futuros = []
        for dias, hora, idx, sigla, nome_proc, status in PROXIMOS:
            prof = self.profs[sigla]
            dia = self._avancar(self.hoje + timedelta(days=dias), self._expediente(sigla), prof)
            if dia in (self.dia_curso, self.folga_bianca) or nome_proc not in self.procs:
                continue
            inicio = _aware(dia, hora)
            if inicio < self.agora + timedelta(minutes=30):
                continue
            pendente = status == 'pendente'
            at = self._reservar(self.clientes[idx], prof, self.procs[nome_proc], inicio,
                                Atendimento.STATUS_PENDENTE if pendente else Atendimento.STATUS_AGENDADO)
            if at is None:
                continue
            if status == 'confirmado':
                at.confirmar(by_user=self.admin)
            futuros.append(at)
        # retorno sem custo do botox da Juliana, ja marcado (o da Camila fica "a marcar" em Retornos)
        idx, nome_proc, dias = RETORNO_MARCADO
        origem = self._origem_do_retorno(Atendimento.objects.filter(cliente=self.clientes[idx])
                                         .select_related('procedimento', 'profissional', 'cliente'), idx, nome_proc)
        if origem is not None:
            dia = self._avancar(self.hoje + timedelta(days=dias), SEG_A_SEX, origem.profissional)
            if dia == self.dia_curso:
                dia = self._avancar(dia + timedelta(days=1), SEG_A_SEX, origem.profissional)
            volta = self._reservar(origem.cliente, origem.profissional, origem.procedimento,
                                   _aware(dia, time(16, 30)), Atendimento.STATUS_AGENDADO,
                                   preco=dict(retorno.PRECO_RETORNO), eh_retorno=True, atendimento_origem=origem)
            if volta is not None:
                futuros.append(volta)
        return futuros

    # ── depois do atendimento ───────────────────────────────────────────

    def _nps(self, passados):
        from aranha_estetica.models import AvaliacaoNPS
        from aranha_estetica.services import nps

        for posicao, (nota, comentario, publica) in NPS.items():
            at = self.por_posicao.get(posicao)
            if at is None or at.status != Atendimento.STATUS_REALIZADO:
                continue
            avaliacao, criada = nps.registrar_avaliacao(at, nota, comentario=comentario,
                                                       autoriza_publicacao=publica)
            if not criada:
                continue
            quando = min(at.data_hora_fim + timedelta(hours=26), self.agora)
            AvaliacaoNPS.objects.filter(pk=avaliacao.pk).update(criado_em=quando)
            if publica:
                avaliacao.aprovar_publicacao()

    def _fichas_e_prontuarios(self, passados):
        from aranha_estetica.services import anamnese

        form = FormularioAnamnese.objects.filter(tipo=FormularioAnamnese.TIPO_ANAMNESE, ativo=True).order_by('pk').first()
        saude = VersaoTermo.saude_vigente()
        vistos = set()
        for at in passados:
            if (form is None or at.cliente_id in vistos or at.eh_retorno
                    or at.status != Atendimento.STATUS_REALIZADO or at.profissional_id != self.jaqueline.pk):
                continue
            vistos.add(at.cliente_id)
            idx = next(i for i, c in enumerate(self.clientes) if c.pk == at.cliente_id)
            respostas, erros = anamnese.validar_respostas(form.schema_json, FICHAS[idx])
            if erros:   # ficha editada pelo painel: a demonstracao nao inventa resposta
                continue
            anamnese.convidar(form, at)
            resposta = RespostaAnamnese.objects.filter(formulario=form, atendimento=at,
                                                       respondida_em__isnull=True).first()
            if resposta is None:
                continue
            resposta.respostas_json = respostas
            resposta.respondida_em = at.data_hora_inicio - timedelta(days=1)
            resposta.save()
            if saude:
                AceiteTermo.registrar(at.cliente, saude, None, at)
            if idx in (ANA, CAMILA):
                self._prontuario(at, FICHAS[idx].get('alergias', ''), ANOTACOES[idx])

    def _prontuario(self, at, alergia, anotacao):
        pront, novo = Prontuario.objects.get_or_create(cliente=at.cliente)
        if novo:
            pront.alergias = alergia
            pront.medicamentos_uso = 'Anticoncepcional oral' if not alergia else ''
            pront.historico_saude = 'Sem doenças crônicas relatadas.'
            pront.observacoes_gerais = 'Pele mista, fototipo III. Prefere resultados naturais.'
            pront.save()
            ProntuarioVersao.registrar(pront, self.admin)
            pront.observacoes_gerais += ' Plano: bioestimulador em 3 sessões.'
            pront.save()
        AnotacaoSessao.objects.create(atendimento=at, autor=self.admin, texto=anotacao)

    def _notificacoes(self, passados, futuros):
        """Lembretes do dia anterior: confirmados nos ultimos realizados e o dos proximos dois dias."""
        realizados = [a for a in passados if a.status == Atendimento.STATUS_REALIZADO]
        for at in realizados[-6:]:
            enviado = at.data_hora_inicio - timedelta(hours=20)
            Notificacao.objects.create(
                atendimento=at, tipo=TipoNotificacao.LEMBRETE, canal=CanalNotificacao.WHATSAPP,
                status=StatusNotificacao.ENVIADO, enviado_em=enviado,
                resposta=RespostaNotificacao.CONFIRMOU, respondido_em=enviado + timedelta(minutes=40))
        for at in futuros:
            if (timezone.localtime(at.data_hora_inicio).date() - self.hoje).days > 1:
                continue
            confirmado = at.status == Atendimento.STATUS_CONFIRMADO
            Notificacao.objects.create(
                atendimento=at, tipo=TipoNotificacao.LEMBRETE, canal=CanalNotificacao.WHATSAPP,
                status=StatusNotificacao.ENVIADO, enviado_em=self.agora - timedelta(hours=3),
                resposta=RespostaNotificacao.CONFIRMOU if confirmado else None,
                respondido_em=self.agora - timedelta(hours=2) if confirmado else None)

    def _comissoes(self):
        """Comissoes ate 3 semanas atras ja pagas; as mais recentes ficam pendentes."""
        from aranha_estetica.services.comissao import ComissaoService

        ate = self.hoje - timedelta(days=21)
        for prof in (self.jaqueline, self.bianca):
            ComissaoService.pagar_pendentes(prof.pk, ate, usuario=self.admin)

    def _lista_espera(self):
        """Um pedido de aviso de vaga (a Camila quer o Preenchimento de Malar na semana que vem)."""
        from aranha_estetica.models import ListaEspera
        from aranha_estetica.services import lista_espera

        proc = self.procs.get('Preenchimento de Malar')
        if proc is None:
            return
        camila = self.clientes[CAMILA]
        try:
            with transaction.atomic():
                lista_espera.inscrever(nome=camila.nome, telefone=camila.telefone, email=None, procedimento=proc,
                                       profissional=self.jaqueline, data_desejada=self.hoje + timedelta(days=5),
                                       turno=ListaEspera.TURNO_TARDE, telefone_verificado=True)
        except Exception:  # noqa: BLE001 — pedido repetido: so pula
            return
