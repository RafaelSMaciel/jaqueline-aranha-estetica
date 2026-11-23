"""Popula o AMBIENTE DE TESTES com uma operacao ficticia proxima da real, para demonstracao.

Depois do `seed --demo` (catalogo, profissional, agenda, termos): ~2,5 meses de historico e as
proximas 2 semanas, pelos mesmos servicos que o sistema usa (agenda.reservar + transicoes da
FSM), para que comissao, debito de pacote, cashback de indicacao, faltas e retornos saiam
coerentes. Cobre todas as telas do painel: agenda, calendario, retornos, clientes, prontuario,
notificacoes, promocoes, anamneses, pacotes, profissionais, financeiro, comissoes, NPS,
bloqueios, lista de espera, termos e o portal da profissional.

    python manage.py popular_demo

- Recusado em producao (mesma regra do `seed --demo`); roda no pre-deploy do environment dev.
- Idempotente: se a primeira cliente da demonstracao ja existe, nao faz nada (varias tabelas
  sao append-only no Postgres: nao ha "refazer").
- Nunca derruba o pre-deploy: erro vira aviso no stderr e a transacao inteira e desfeita.
- Nada sai para fora: e-mails @example.com, celulares ficticios (11 90000-0xxx) e nenhum servico
  que enfileira e-mail ou WhatsApp e chamado.
- O horario das 10:00 fica sempre livre nos proximos dias (roteiro da apresentacao).
- Login da 2a profissional (portal /profissional/): DEMO_PROFISSIONAL_EMAIL/DEMO_PROFISSIONAL_SENHA
  na env; sem a senha a conta e criada sem senha utilizavel.
"""
import os
import random
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
    FormularioAnamnese, Habilitacao, ItemPacote, ListaEspera, Pacote, Preco, Procedimento,
    Profissional, Promocao, Prontuario, ProntuarioVersao, RegraComissao, RespostaAnamnese,
    Usuario, VersaoTermo,
)
from aranha_estetica.models.notificacoes import (
    CanalNotificacao, Notificacao, RespostaNotificacao, StatusNotificacao, TipoNotificacao,
)
from aranha_estetica.models.termos import AceiteTermo

SEMENTE = 2026
DIAS_HISTORICO = 75
DIAS_FUTURO = 14

# (nome, celular, nascimento) — celulares ficticios na faixa 11 90000-01xx
CLIENTES = [
    ('Ana Beatriz Moura', '11900000101', date(1991, 3, 14)),
    ('Camila Ferreira Santos', '11900000102', date(1988, 7, 2)),
    ('Juliana Rocha Lima', '11900000103', date(1995, 11, 25)),
    ('Patrícia Almeida Costa', '11900000104', date(1979, 1, 9)),
    ('Fernanda Oliveira Prado', '11900000105', date(1993, 5, 30)),
    ('Larissa Mendes Ribeiro', '11900000106', date(1998, 9, 18)),
    ('Mariana Castro Dias', '11900000107', date(1986, 12, 3)),
    ('Renata Souza Barbosa', '11900000108', date(1982, 4, 21)),
    ('Gabriela Nunes Teixeira', '11900000109', date(1997, 8, 11)),
    ('Aline Cardoso Martins', '11900000110', date(1990, 2, 27)),
    ('Beatriz Gomes Araújo', '11900000111', date(2000, 6, 6)),
    ('Carolina Pires Monteiro', '11900000112', date(1984, 10, 15)),
    ('Daniela Freitas Rezende', '11900000113', date(1989, 3, 1)),
    ('Eduarda Lopes Carvalho', '11900000114', date(2001, 1, 19)),
    ('Isabela Ramos Vieira', '11900000115', date(1994, 7, 23)),
    ('Letícia Batista Moreira', '11900000116', date(1992, 11, 8)),
    ('Natália Correia Fonseca', '11900000117', date(1987, 5, 12)),
    ('Priscila Andrade Melo', '11900000118', date(1981, 9, 29)),
    ('Tatiane Azevedo Cunha', '11900000119', date(1996, 4, 4)),
    ('Vanessa Duarte Campos', '11900000120', date(1985, 8, 17)),
    ('Rafaela Machado Lacerda', '11900000121', date(1999, 12, 22)),
    ('Sabrina Torres Guimarães', '11900000122', date(1990, 6, 13)),
    ('Lucas Henrique Farias', '11900000123', date(1992, 2, 5)),
    ('Thiago Moraes Siqueira', '11900000124', date(1988, 10, 31)),
]
INDICACOES = {5: 0, 12: 3, 20: 8}   # indice da indicada -> indice de quem indicou (cashback)
NOVAS_DO_MES = (19, 20, 21, 22)       # cadastro neste mes (indicador "novas clientes")

PROF2 = {
    'nome': 'Bianca Moraes',
    'especialidade': 'Esteticista · Limpeza de pele, drenagem e massagens',
    'bio': 'Esteticista com foco em cuidados corporais e faciais não invasivos.',
}
PROF2_PROCEDIMENTOS = ('Limpeza de Pele', 'Drenagem Linfática Corporal', 'Drenagem Linfática Facial',
                       'Endermoterapia', 'Ventosaterapia', 'Massagem Relaxante')
PROF2_EMAIL_PADRAO = 'bianca@demo.jaquelinearanha.test'
REGRA_PROF2 = Decimal('40.00')

PROCS_JAQUELINE = ('Botox 1 região', 'Botox 2 regiões', 'Botox 3 regiões', 'Preenchimento Labial',
                   'Preenchimento Bigode Chinês', 'Preenchimento de Malar', 'Bioestimulador de Colágeno',
                   'Skinbooster', 'Microagulhamento', 'PRP (Fibrina)', 'Enzimas para Gordura Localizada')
BOTOX = ('Botox 1 região', 'Botox 2 regiões', 'Botox 3 regiões')
RETORNO_DIAS = (10, 20)               # janela do retorno do botox (ajuste de dose)

# 10:00 fica livre (o roteiro da apresentacao agenda nesse horario)
HORAS_JAQUELINE = (time(9, 0), time(11, 0), time(14, 0), time(15, 30), time(16, 30))
HORAS_BIANCA = (time(9, 0), time(11, 0), time(13, 30), time(15, 0), time(16, 30))

PACOTES = [
    # (nome, procedimento, sessoes, preco_total, descricao)
    ('Drenagem Linfática — 10 sessões', 'Drenagem Linfática Corporal', 10, '1000.00',
     'Dez sessões de drenagem corporal com valor especial.'),
    ('Enzimas Gordura Localizada — 8 sessões', 'Enzimas para Gordura Localizada', 8, '840.00',
     'Protocolo de oito aplicações de enzimas.'),
]

NPS_POSITIVOS = [
    'Atendimento impecável, me senti muito acolhida. Resultado natural!',
    'A Jaqueline é muito cuidadosa e explica tudo antes. Amei o resultado.',
    'Ambiente lindo e tranquilo. Já marquei a próxima sessão.',
    'Profissional excelente, pontual e atenciosa.',
    'Melhor clínica que já fui. Recomendo de olhos fechados!',
    'Saí renovada da drenagem, a Bianca tem mãos de fada.',
    'Resultado superou minhas expectativas, muito natural.',
    'Atendimento personalizado do começo ao fim.',
]
NPS_NEUTROS = ['Gostei, só achei que atrasou um pouco.', 'Bom atendimento, o estacionamento é difícil.']
NPS_DETRATORES = ['Tive que esperar 30 minutos além do horário.',
                  'Esperava um resultado mais visível na primeira sessão.']

ANOTACOES = [
    'Aplicação conforme planejado. Orientada a não deitar nas próximas 4 horas.',
    'Pele reagiu bem, leve eritema esperado. Retorno conforme protocolo.',
    'Cliente relatou sensibilidade moderada; aplicada anestesia tópica.',
    'Evolução positiva em relação à sessão anterior. Fotos registradas.',
]


def _aware(dia, hora):
    return timezone.make_aware(datetime.combine(dia, hora), timezone.get_current_timezone())


def _email(nome):
    import unicodedata
    base = unicodedata.normalize('NFKD', nome).encode('ascii', 'ignore').decode().lower().split()
    return f'{base[0]}.{base[-1]}@example.com'


class Command(BaseCommand):
    help = 'Popula o ambiente de testes com uma operação fictícia completa (idempotente; nunca em produção).'

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
        self.rng = random.Random(SEMENTE)
        self.hoje = timezone.localdate()
        self.agora = timezone.now()
        self.faltas = {}
        self.retornos_feitos = 0

    # ── orquestracao ────────────────────────────────────────────────────

    def executar(self) -> str:
        self.jaqueline = Profissional.objects.get(nome='Jaqueline Aranha')
        self.procs = {p.nome: p for p in Procedimento.objects.filter(ativo=True)}
        self._retorno_do_botox()
        self.bianca = self._segunda_profissional()
        self.clientes = self._clientes()
        self.termo_labial = self._termo_procedimento('Preenchimento Labial')
        self.compras = self._pacotes()
        self._bloqueios()
        self._promocoes()
        passados = self._historico()
        futuros = self._proximos_dias()
        self._nps(passados)
        self._fichas_e_prontuarios(passados)
        self._notificacoes(passados, futuros)
        self._comissoes()
        self._lista_espera()
        return (f'{len(self.clientes)} clientes, {len(passados)} atendimentos no histórico, '
                f'{len(futuros)} nos próximos dias, {self.retornos_feitos} retornos.')

    # ── cadastros ───────────────────────────────────────────────────────

    def _retorno_do_botox(self):
        for nome in BOTOX:
            proc = self.procs.get(nome)
            if proc and not proc.exige_retorno:
                proc.exige_retorno = True
                proc.retorno_minimo_dias, proc.retorno_maximo_dias = RETORNO_DIAS
                proc.save(update_fields=['exige_retorno', 'retorno_minimo_dias', 'retorno_maximo_dias'])

    def _segunda_profissional(self):
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
        inicio_mes = self.hoje.replace(day=1)
        clientes = []
        for i, (nome, telefone, nascimento) in enumerate(CLIENTES):
            cliente = Cliente.objects.create(nome=nome, telefone=telefone, email=_email(nome),
                                             data_nascimento=nascimento)
            if i in NOVAS_DO_MES:
                criado = _aware(inicio_mes + timedelta(days=min(i - 18, (self.hoje - inicio_mes).days)), time(10))
            else:
                criado = _aware(self.hoje - timedelta(days=DIAS_HISTORICO + 5 + 4 * (len(CLIENTES) - i)), time(10))
            Cliente.objects.filter(pk=cliente.pk).update(criado_em=criado)
            clientes.append(cliente)
            if lgpd:
                AceiteTermo.registrar(cliente, lgpd)
            origem = cons.Consentimento.ORIGEM_AGENDAMENTO_ONLINE
            cons.registrar(cliente, *cons.LEMBRETE_WHATSAPP, True, origem=origem, registrado_em=criado)
            if self.rng.random() < 0.6:
                cons.registrar(cliente, *cons.PESQUISA_WHATSAPP, True, origem=origem, registrado_em=criado)
            if self.rng.random() < 0.5:
                cons.registrar(cliente, *cons.MARKETING_EMAIL, True, origem=origem, registrado_em=criado)
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
                vigente_desde=self.hoje - timedelta(days=DIAS_HISTORICO + 10),
                titulo=f'Termo de consentimento — {nome_proc}',
                conteudo=(f'Declaro que fui informada sobre o procedimento de {nome_proc.lower()}, seus '
                          'cuidados, possíveis efeitos (inchaço, hematomas e sensibilidade temporários) e '
                          'contraindicações, e que tive minhas dúvidas esclarecidas pela profissional.'),
            )
        return termo

    def _pacotes(self):
        compras = {}
        for nome, nome_proc, sessoes, total, descricao in PACOTES:
            proc = self.procs.get(nome_proc)
            if proc is None:
                continue
            pacote, novo = Pacote.objects.get_or_create(nome=nome, defaults={
                'descricao': descricao, 'preco_total': Decimal(total)})
            if novo:
                ItemPacote.objects.create(pacote=pacote, procedimento=proc, quantidade_sessoes=sessoes)
            compras[nome_proc] = pacote
        return compras

    def _bloqueios(self):
        self.dia_curso = self._dia_util(self.hoje + timedelta(days=8), (0, 1, 2, 3, 4))
        bloqueio = AgendaExcecao(
            tipo=AgendaExcecao.TIPO_BLOQUEIO, profissional=self.jaqueline,
            data_hora_inicio=_aware(self.dia_curso, time(13, 0)), data_hora_fim=_aware(self.dia_curso, time(18, 0)),
            motivo='Curso de atualização em harmonização facial', usuario_autor=self.admin)
        bloqueio.full_clean()
        bloqueio.save()
        self.folga_bianca = self._dia_util(self.hoje + timedelta(days=5), (1, 2, 3, 4))
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

    def _dia_util(self, dia, dias_semana):
        while dia.weekday() not in dias_semana or self._feriado(dia):
            dia += timedelta(days=1)
        return dia

    def _feriado(self, dia, prof=None):
        qs = AgendaExcecao.objects.filter(tipo=AgendaExcecao.TIPO_FOLGA, ativo=True,
                                          data_inicio__lte=dia, data_fim__gte=dia)
        if prof is None:
            return qs.filter(profissional__isnull=True).exists()
        return qs.filter(profissional__isnull=True).exists() or qs.filter(profissional=prof).exists()

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

    def _agenda_do_dia(self, dia, prof, horas, procs, n):
        """[(cliente, prof, proc, inicio)] para ate n horarios do dia."""
        pendentes = []
        for hora in self.rng.sample(horas, k=min(n, len(horas))):
            if prof == self.jaqueline and dia == self.dia_curso and hora >= time(13, 0):
                continue
            cliente = self.rng.choice(self.clientes)
            nome_proc = self.rng.choice(procs)
            if nome_proc in self.procs:
                pendentes.append((cliente, prof, self.procs[nome_proc], _aware(dia, hora)))
        return pendentes

    def _historico(self):
        from aranha_estetica.services import atendimento as svc

        planos = []
        inicio = self.hoje - timedelta(days=DIAS_HISTORICO)
        for d in range(DIAS_HISTORICO):
            dia = inicio + timedelta(days=d)
            if dia.weekday() <= 4 and not self._feriado(dia, self.jaqueline):
                planos += self._agenda_do_dia(dia, self.jaqueline, HORAS_JAQUELINE, PROCS_JAQUELINE,
                                              self.rng.choice((1, 2, 2, 3)))
            if 1 <= dia.weekday() <= 5 and not self._feriado(dia, self.bianca):
                planos += self._agenda_do_dia(dia, self.bianca, HORAS_BIANCA, PROF2_PROCEDIMENTOS,
                                              self.rng.choice((1, 1, 2)))
        planos += self._sessoes_de_pacote()
        planos.sort(key=lambda p: p[3])

        feitos = []
        for cliente, prof, proc, inicio_at in planos:
            if inicio_at >= self.agora - timedelta(hours=1):
                continue
            at = self._reservar(cliente, prof, proc, inicio_at, Atendimento.STATUS_AGENDADO)
            if at is None:
                continue
            sorte = self.rng.random()
            if sorte < 0.07 and self.faltas.get(cliente.pk, 0) < 2:
                at.marcar_falta(by_user=self.admin)
                self.faltas[cliente.pk] = self.faltas.get(cliente.pk, 0) + 1
            elif sorte < 0.15:
                at.cancelar(motivo='Cliente pediu para desmarcar', by_user=self.admin)
            else:
                if self.termo_labial and proc == self.termo_labial.procedimento:
                    AceiteTermo.registrar(cliente, self.termo_labial, None, at)
                svc.marcar_realizado(at, por=self.admin, aceitar_sem_termo=True)
                self.faltas[cliente.pk] = 0
                self._retorno_se_couber(at)
            feitos.append(at)
        return feitos

    def _sessoes_de_pacote(self):
        """3 vendas: duas antigas (sessoes semanais ja feitas) e uma deste mes."""
        planos = []
        vendas = [(1, 'Drenagem Linfática Corporal', 63, self.bianca),
                  (6, 'Drenagem Linfática Corporal', 45, self.bianca),
                  (9, 'Enzimas para Gordura Localizada', 12, self.jaqueline)]
        for idx, nome_proc, dias_atras, prof in vendas:
            pacote = self.compras.get(nome_proc)
            if pacote is None:
                continue
            cliente = self.clientes[idx]
            compra = CompraPacote.vender(pacote, cliente, pacote.preco_total)
            vendido = _aware(self.hoje - timedelta(days=dias_atras), time(9, 30))
            CompraPacote.objects.filter(pk=compra.pk).update(criado_em=vendido)
            dia = self.hoje - timedelta(days=dias_atras - 2)
            dias_semana = (1, 2, 3, 4, 5) if prof == self.bianca else (0, 1, 2, 3, 4)
            while dia < self.hoje:
                dia = self._dia_util(dia, dias_semana)
                if dia >= self.hoje:
                    break
                planos.append((cliente, prof, self.procs[nome_proc], _aware(dia, time(16, 30))))
                dia += timedelta(days=7)
        return planos

    def _retorno_se_couber(self, origem):
        """Botox: retorno feito para os antigos, um agendado e os recentes ficam 'devidos'."""
        from aranha_estetica.services import atendimento as svc
        from aranha_estetica.services import retorno

        if origem.procedimento.nome not in BOTOX:
            return
        dia = timezone.localtime(origem.data_hora_inicio).date() + timedelta(days=14)
        if dia >= self.hoje - timedelta(days=1):
            return
        dia = self._dia_util(dia, (0, 1, 2, 3, 4))
        if dia >= self.hoje:
            return
        volta = self._reservar(origem.cliente, origem.profissional, origem.procedimento, _aware(dia, time(11, 0)),
                               Atendimento.STATUS_AGENDADO, preco=dict(retorno.PRECO_RETORNO),
                               eh_retorno=True, atendimento_origem=origem)
        if volta is not None:
            svc.marcar_realizado(volta, por=self.admin, aceitar_sem_termo=True)
            self.retornos_feitos += 1

    def _proximos_dias(self):
        from aranha_estetica.services import retorno

        planos = []
        for d in range(0, DIAS_FUTURO + 1):
            dia = self.hoje + timedelta(days=d)
            if dia.weekday() <= 4 and not self._feriado(dia, self.jaqueline):
                planos += self._agenda_do_dia(dia, self.jaqueline, HORAS_JAQUELINE, PROCS_JAQUELINE,
                                              3 if d == 1 else self.rng.choice((1, 2, 2)))
            if 1 <= dia.weekday() <= 5 and dia != self.folga_bianca and not self._feriado(dia, self.bianca):
                planos += self._agenda_do_dia(dia, self.bianca, HORAS_BIANCA, PROF2_PROCEDIMENTOS,
                                              2 if d == 1 else self.rng.choice((1, 1, 2)))
        planos.sort(key=lambda p: p[3])
        futuros = []
        pedidos_online = 0
        for cliente, prof, proc, inicio in planos:
            if inicio < self.agora + timedelta(hours=2):
                continue
            dias = (timezone.localtime(inicio).date() - self.hoje).days
            status = Atendimento.STATUS_AGENDADO
            if dias >= 3 and pedidos_online < 3 and self.rng.random() < 0.3:
                status = Atendimento.STATUS_PENDENTE
                pedidos_online += 1
            at = self._reservar(cliente, prof, proc, inicio, status)
            if at is None:
                continue
            if status == Atendimento.STATUS_AGENDADO and dias <= 2 and self.rng.random() < 0.6:
                at.confirmar(by_user=self.admin)
            futuros.append(at)
        # um retorno de botox ja marcado (os demais ficam na tela "Retornos" como devidos)
        for devido in retorno.devidos()[:1]:
            origem = devido.origem
            dia = max(devido.de, self._dia_util(self.hoje + timedelta(days=3), (0, 1, 2, 3, 4)))
            if dia == self.dia_curso:
                dia = self._dia_util(dia + timedelta(days=1), (0, 1, 2, 3, 4))
            if dia > devido.ate:
                continue
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

        publicados = 0
        for at in passados:
            if at.status != Atendimento.STATUS_REALIZADO or at.eh_retorno or self.rng.random() > 0.55:
                continue
            sorte = self.rng.random()
            if sorte < 0.06:
                nota, comentario, autoriza = self.rng.choice((3, 5, 6)), self.rng.choice(NPS_DETRATORES), False
            elif sorte < 0.2:
                nota, comentario, autoriza = self.rng.choice((7, 8)), self.rng.choice(NPS_NEUTROS), False
            else:
                nota = self.rng.choice((9, 10, 10))
                comentario = self.rng.choice(NPS_POSITIVOS) if self.rng.random() < 0.6 else ''
                autoriza = bool(comentario) and self.rng.random() < 0.7
            avaliacao, criada = nps.registrar_avaliacao(at, nota, comentario=comentario,
                                                       autoriza_publicacao=autoriza)
            if not criada:
                continue
            quando = min(at.data_hora_fim + timedelta(hours=26), self.agora)
            AvaliacaoNPS.objects.filter(pk=avaliacao.pk).update(criado_em=quando)
            if autoriza and publicados < 8:
                avaliacao.aprovar_publicacao()
                publicados += 1

    def _fichas_e_prontuarios(self, passados):
        from aranha_estetica.services import anamnese

        form = FormularioAnamnese.objects.filter(tipo=FormularioAnamnese.TIPO_ANAMNESE, ativo=True).order_by('pk').first()
        saude = VersaoTermo.saude_vigente()
        vistos = set()
        injetaveis = [a for a in passados if a.status == Atendimento.STATUS_REALIZADO
                      and a.profissional_id == self.jaqueline.pk and not a.eh_retorno]
        for at in injetaveis:
            if at.cliente_id in vistos or form is None:
                continue
            vistos.add(at.cliente_id)
            alergia = 'Lidocaína' if len(vistos) == 2 else ('Dipirona' if len(vistos) == 5 else '')
            dados = {'gestante_ou_amamentando': False, 'alergias': alergia,
                     'uso_de_acidos': len(vistos) % 3 == 0, 'procedimentos_recentes': ''}
            anamnese.convidar(form, at)
            resposta = RespostaAnamnese.objects.filter(formulario=form, atendimento=at,
                                                       respondida_em__isnull=True).first()
            if resposta is None:
                continue
            resposta.respostas_json = anamnese.validar_respostas(form.schema_json, dados)[0]
            resposta.respondida_em = at.data_hora_inicio - timedelta(days=1)
            resposta.save()
            if saude:
                AceiteTermo.registrar(at.cliente, saude, None, at)
            if len(vistos) <= 6:
                self._prontuario(at, alergia)

    def _prontuario(self, at, alergia):
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
        AnotacaoSessao.objects.create(atendimento=at, autor=self.admin, texto=self.rng.choice(ANOTACOES))

    def _notificacoes(self, passados, futuros):
        for at in passados[-25:]:
            if at.status == Atendimento.STATUS_REALIZADO:
                enviado = at.data_hora_inicio - timedelta(hours=20)
                Notificacao.objects.create(
                    atendimento=at, tipo=TipoNotificacao.LEMBRETE, canal=CanalNotificacao.WHATSAPP,
                    status=StatusNotificacao.ENVIADO, enviado_em=enviado,
                    resposta=RespostaNotificacao.CONFIRMOU, respondido_em=enviado + timedelta(minutes=40))
        for i, at in enumerate(futuros):
            if (timezone.localtime(at.data_hora_inicio).date() - self.hoje).days > 1:
                continue
            confirmado = at.status == Atendimento.STATUS_CONFIRMADO
            Notificacao.objects.create(
                atendimento=at, tipo=TipoNotificacao.LEMBRETE, canal=CanalNotificacao.WHATSAPP,
                status=StatusNotificacao.FALHOU if i == 0 and not confirmado else StatusNotificacao.ENVIADO,
                enviado_em=self.agora - timedelta(hours=3),
                resposta=RespostaNotificacao.CONFIRMOU if confirmado else None,
                respondido_em=self.agora - timedelta(hours=2) if confirmado else None)

    def _comissoes(self):
        from aranha_estetica.services.comissao import ComissaoService

        ate = self.hoje - timedelta(days=30)
        for prof in (self.jaqueline, self.bianca):
            ComissaoService.pagar_pendentes(prof.pk, ate, usuario=self.admin)

    def _lista_espera(self):
        from aranha_estetica.services import lista_espera

        pedidos = [
            ('Helena Cristina Paiva', '11900000201', 'Botox 1 região', 2, ListaEspera.TURNO_MANHA, False),
            ('Michele Santana Rios', '11900000202', 'Limpeza de Pele', 3, ListaEspera.TURNO_TARDE, False),
            (self.clientes[2].nome, self.clientes[2].telefone, 'Skinbooster', 4, None, True),
            (self.clientes[14].nome, self.clientes[14].telefone, 'Drenagem Linfática Corporal', 6,
             ListaEspera.TURNO_MANHA, True),
        ]
        for nome, telefone, nome_proc, dias, turno, verificado in pedidos:
            proc = self.procs.get(nome_proc)
            if proc is None:
                continue
            prof = self.bianca if nome_proc in PROF2_PROCEDIMENTOS else self.jaqueline
            try:
                with transaction.atomic():
                    lista_espera.inscrever(nome=nome, telefone=telefone, email=None, procedimento=proc,
                                           profissional=prof, data_desejada=self.hoje + timedelta(days=dias),
                                           turno=turno, telefone_verificado=verificado)
            except Exception:  # noqa: BLE001 — pedido repetido: so pula
                continue
