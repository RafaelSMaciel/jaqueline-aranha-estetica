"""
seed.py — Gera dados de teste para desenvolvimento local.

ATENCAO: Este arquivo NAO deve ser executado em producao.
Ele cria dados fictícios, incluindo um usuario admin com senha fraca.
"""
import os
import sys
import django
from datetime import time, date, datetime, timedelta
from django.utils import timezone as tz
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shivazen.settings')
django.setup()

from django.conf import settings as django_settings

# ═══ PROTECAO: Bloqueia execucao em producao ═══
if not django_settings.DEBUG:
    print("\n[BLOQUEADO] seed.py so pode ser executado com DEBUG=True.")
    print("Este script cria dados ficticios e NAO deve rodar em producao.\n")
    sys.exit(1)

from app_shivazen.models import (
    Profissional, Procedimento, ProfissionalProcedimento,
    DisponibilidadeProfissional, Cliente, Atendimento,
    Promocao, Preco, Pacote, ItemPacote, ProntuarioPergunta,
    Venda, Orcamento, CategoriaProduto, Produto, MovimentacaoEstoque,
    ConfiguracaoSistema, Perfil, Usuario
)


def seed():
    print("Iniciando seed de dados de teste...")

    # ──────────────────────────────────────────
    # 0. ADMIN USER (criado primeiro para garantir acesso ao painel)
    # ──────────────────────────────────────────
    try:
        perfil_admin, _ = Perfil.objects.get_or_create(
            nome='Administrador',
            defaults={'descricao': 'Acesso total a todas as funcionalidades do sistema.'}
        )
        admin_user, created = Usuario.objects.get_or_create(
            email='admin@shivazen.com',
            defaults={
                'nome': 'Administrador',
                'perfil': perfil_admin,
                'ativo': True,
            }
        )
        if created:
            admin_user.set_password('admin123')
            admin_user.save()
            print("  Admin: Criado (admin@shivazen.com / admin123)")
        else:
            # Garante que a senha esteja correta mesmo se o user ja existia
            admin_user.set_password('admin123')
            admin_user.perfil = perfil_admin
            admin_user.save()
            print("  Admin: Já existente (senha resetada)")
    except Exception as e:
        print(f"  Admin: Erro ao criar ({e})")

    # ──────────────────────────────────────────
    # 1. PROFISSIONAIS
    # ──────────────────────────────────────────
    prof1, _ = Profissional.objects.get_or_create(
        nome='Dra. Stefany',
        defaults={'especialidade': 'Estética Avançada', 'ativo': True}
    )
    prof2, _ = Profissional.objects.get_or_create(
        nome='Dra. Amanda Costa',
        defaults={'especialidade': 'Harmonização Facial', 'ativo': True}
    )
    prof3, _ = Profissional.objects.get_or_create(
        nome='Camila Oliveira',
        defaults={'especialidade': 'Massoterapia e Estética Corporal', 'ativo': True}
    )

    profissionais = [prof1, prof2, prof3]
    print(f"  Profissionais: {len(profissionais)} criados/existentes")

    # ──────────────────────────────────────────
    # 2. PROCEDIMENTOS (4 básicos para demonstração)
    # ──────────────────────────────────────────
    procedimentos_data = [
        {'nome': 'Limpeza de Pele Profunda', 'descricao': 'Remoção de impurezas, cravos e células mortas com técnicas especializadas. Inclui extração, tonificação e máscara hidratante.', 'duracao_minutos': 60},
        {'nome': 'Peeling Químico', 'descricao': 'Renovação celular com ácidos específicos para eliminar manchas, cicatrizes de acne e uniformizar a textura da pele.', 'duracao_minutos': 45},
        {'nome': 'Drenagem Linfática', 'descricao': 'Técnica manual para estimular o sistema linfático, reduzir inchaço e eliminar toxinas do corpo.', 'duracao_minutos': 60},
        {'nome': 'Massagem Relaxante', 'descricao': 'Técnicas para alívio do estresse, tensão muscular e promoção do bem-estar integral.', 'duracao_minutos': 60},
    ]

    procedimentos = []
    for pd in procedimentos_data:
        proc, _ = Procedimento.objects.get_or_create(
            nome=pd['nome'],
            defaults={'descricao': pd['descricao'], 'duracao_minutos': pd['duracao_minutos'], 'ativo': True}
        )
        procedimentos.append(proc)

    print(f"  Procedimentos: {len(procedimentos)} criados/existentes")

    # ──────────────────────────────────────────
    # 3. VINCULAR PROFISSIONAIS AOS PROCEDIMENTOS
    # ──────────────────────────────────────────
    # Dra. Stefany: todos os procedimentos
    for proc in procedimentos:
        ProfissionalProcedimento.objects.get_or_create(profissional=prof1, procedimento=proc)

    # Dra. Amanda: Limpeza de Pele e Peeling (faciais)
    for proc in procedimentos[:2]:
        ProfissionalProcedimento.objects.get_or_create(profissional=prof2, procedimento=proc)

    # Camila: Drenagem e Massagem (corporais)
    for proc in procedimentos[2:]:
        ProfissionalProcedimento.objects.get_or_create(profissional=prof3, procedimento=proc)

    print("  Vínculos profissional-procedimento criados")

    # ──────────────────────────────────────────
    # 4. DISPONIBILIDADE
    # ──────────────────────────────────────────
    for prof in profissionais:
        # Segunda a Sexta
        dias_semana = [2, 3, 4, 5, 6]
        for dia in dias_semana:
            DisponibilidadeProfissional.objects.get_or_create(
                profissional=prof,
                dia_semana=dia,
                defaults={'hora_inicio': time(9, 0), 'hora_fim': time(18, 0)}
            )
        # Sábado
        DisponibilidadeProfissional.objects.get_or_create(
            profissional=prof,
            dia_semana=7,
            defaults={'hora_inicio': time(9, 0), 'hora_fim': time(14, 0)}
        )

    print("  Disponibilidades criadas (Seg-Sáb)")

    # ──────────────────────────────────────────
    # 5. PREÇOS
    # ──────────────────────────────────────────
    precos_base = {
        'Limpeza de Pele Profunda': 180,
        'Peeling Químico': 350,
        'Drenagem Linfática': 180,
        'Massagem Relaxante': 180,
    }

    for proc in procedimentos:
        if proc.nome in precos_base:
            Preco.objects.get_or_create(
                procedimento=proc,
                profissional=None,
                defaults={'valor': Decimal(str(precos_base[proc.nome])), 'descricao': f'Preço padrão - {proc.nome}'}
            )

    print(f"  Preços: {len(precos_base)} preços base criados")

    # ──────────────────────────────────────────
    # 6. CLIENTES DE TESTE
    # ──────────────────────────────────────────
    clientes_data = [
        {'nome_completo': 'Maria Silva Santos', 'telefone': '(17) 99999-0001', 'email': 'maria.silva@teste.com', 'cpf': '111.111.111-11', 'data_nascimento': date(1990, 3, 15)},
        {'nome_completo': 'Ana Paula Oliveira', 'telefone': '(17) 99999-0002', 'email': 'ana.oliveira@teste.com', 'cpf': '222.222.222-22', 'data_nascimento': date(1985, 7, 22)},
        {'nome_completo': 'Juliana Costa Ferreira', 'telefone': '(17) 99999-0003', 'email': 'juliana.costa@teste.com', 'cpf': '333.333.333-33', 'data_nascimento': date(1992, 11, 8)},
        {'nome_completo': 'Fernanda Almeida Lima', 'telefone': '(17) 99999-0004', 'email': 'fernanda.lima@teste.com', 'cpf': '444.444.444-44', 'data_nascimento': date(1988, 1, 30)},
        {'nome_completo': 'Camila Rodrigues Souza', 'telefone': '(17) 99999-0005', 'email': 'camila.souza@teste.com', 'cpf': '555.555.555-55', 'data_nascimento': date(1995, 5, 12)},
        {'nome_completo': 'Patrícia Mendes Rocha', 'telefone': '(17) 99999-0006', 'email': 'patricia.rocha@teste.com', 'cpf': '666.666.666-66', 'data_nascimento': date(1993, 9, 4)},
        {'nome_completo': 'Larissa Barbosa Nunes', 'telefone': '(17) 99999-0007', 'email': 'larissa.nunes@teste.com', 'cpf': '777.777.777-77', 'data_nascimento': date(1991, 12, 18)},
        {'nome_completo': 'Beatriz Carvalho Dias', 'telefone': '(17) 99999-0008', 'email': 'beatriz.dias@teste.com', 'cpf': '888.888.888-88', 'data_nascimento': date(1987, 6, 25)},
        {'nome_completo': 'Renata Martins Pereira', 'telefone': '(17) 99999-0009', 'email': 'renata.pereira@teste.com', 'cpf': '999.999.999-99', 'data_nascimento': date(1994, 2, 14)},
        {'nome_completo': 'Isabela Gonçalves Reis', 'telefone': '(17) 99999-0010', 'email': 'isabela.reis@teste.com', 'cpf': '000.000.000-00', 'data_nascimento': date(1996, 8, 7)},
    ]

    clientes = []
    for cd in clientes_data:
        cliente, _ = Cliente.objects.get_or_create(
            cpf=cd['cpf'],
            defaults={
                'nome_completo': cd['nome_completo'],
                'telefone': cd['telefone'],
                'email': cd['email'],
                'data_nascimento': cd['data_nascimento'],
                'ativo': True,
            }
        )
        clientes.append(cliente)

    print(f"  Clientes: {len(clientes)} criados/existentes")

    # ──────────────────────────────────────────
    # 7. AGENDAMENTOS DE TESTE
    # ──────────────────────────────────────────
    hoje = date.today()
    agendamentos_data = [
        # Hoje
        {'cliente': clientes[0], 'profissional': prof1, 'procedimento': procedimentos[0], 'hora': time(9, 0), 'dia_offset': 0, 'status': 'AGENDADO'},
        {'cliente': clientes[1], 'profissional': prof2, 'procedimento': procedimentos[1], 'hora': time(10, 30), 'dia_offset': 0, 'status': 'CONFIRMADO'},
        {'cliente': clientes[2], 'profissional': prof3, 'procedimento': procedimentos[2], 'hora': time(14, 0), 'dia_offset': 0, 'status': 'AGENDADO'},
        # Amanhã
        {'cliente': clientes[3], 'profissional': prof1, 'procedimento': procedimentos[3], 'hora': time(9, 0), 'dia_offset': 1, 'status': 'AGENDADO'},
        {'cliente': clientes[4], 'profissional': prof2, 'procedimento': procedimentos[0], 'hora': time(11, 0), 'dia_offset': 1, 'status': 'AGENDADO'},
        {'cliente': clientes[5], 'profissional': prof3, 'procedimento': procedimentos[2], 'hora': time(14, 0), 'dia_offset': 1, 'status': 'CONFIRMADO'},
        # Próximos dias
        {'cliente': clientes[6], 'profissional': prof1, 'procedimento': procedimentos[1], 'hora': time(10, 0), 'dia_offset': 2, 'status': 'AGENDADO'},
        {'cliente': clientes[7], 'profissional': prof3, 'procedimento': procedimentos[3], 'hora': time(15, 0), 'dia_offset': 2, 'status': 'AGENDADO'},
        {'cliente': clientes[8], 'profissional': prof1, 'procedimento': procedimentos[0], 'hora': time(9, 0), 'dia_offset': 3, 'status': 'AGENDADO'},
        {'cliente': clientes[9], 'profissional': prof2, 'procedimento': procedimentos[1], 'hora': time(11, 0), 'dia_offset': 3, 'status': 'AGENDADO'},
        # Passados (concluídos/cancelados)
        {'cliente': clientes[0], 'profissional': prof1, 'procedimento': procedimentos[2], 'hora': time(10, 0), 'dia_offset': -2, 'status': 'CONCLUIDO'},
        {'cliente': clientes[1], 'profissional': prof2, 'procedimento': procedimentos[1], 'hora': time(14, 0), 'dia_offset': -3, 'status': 'CONCLUIDO'},
        {'cliente': clientes[2], 'profissional': prof3, 'procedimento': procedimentos[3], 'hora': time(9, 0), 'dia_offset': -1, 'status': 'CANCELADO'},
        {'cliente': clientes[3], 'profissional': prof1, 'procedimento': procedimentos[0], 'hora': time(16, 0), 'dia_offset': -5, 'status': 'CONCLUIDO'},
        {'cliente': clientes[4], 'profissional': prof3, 'procedimento': procedimentos[2], 'hora': time(11, 0), 'dia_offset': -7, 'status': 'CONCLUIDO'},
    ]

    agendamentos_criados = 0
    for ad in agendamentos_data:
        data_agend = hoje + timedelta(days=ad['dia_offset'])
        dt_inicio = tz.make_aware(datetime.combine(data_agend, ad['hora']))
        dt_fim = dt_inicio + timedelta(minutes=ad['procedimento'].duracao_minutos)

        preco = precos_base.get(ad['procedimento'].nome, 200)

        _, created = Atendimento.objects.get_or_create(
            cliente=ad['cliente'],
            profissional=ad['profissional'],
            procedimento=ad['procedimento'],
            data_hora_inicio=dt_inicio,
            defaults={
                'data_hora_fim': dt_fim,
                'valor_cobrado': Decimal(str(preco)),
                'status_atendimento': ad['status'],
            }
        )
        if created:
            agendamentos_criados += 1

    print(f"  Agendamentos: {agendamentos_criados} novos criados")

    # ──────────────────────────────────────────
    # 8. PROMOÇÕES
    # ──────────────────────────────────────────
    promocoes_data = [
        {
            'nome': 'Limpeza de Pele com 30% OFF',
            'descricao': 'Aproveite nossa promoção de limpeza de pele profunda com 30% de desconto. Válido para novos e antigos clientes.',
            'procedimento': procedimentos[0],
            'desconto_percentual': 30,
            'preco_promocional': Decimal('126.00'),
            'data_inicio': hoje - timedelta(days=5),
            'data_fim': hoje + timedelta(days=25),
        },
        {
            'nome': 'Massagem Relaxante - Semana do Bem-estar',
            'descricao': 'Na semana do bem-estar, massagem relaxante com valor especial. Cuide do seu corpo e mente.',
            'procedimento': procedimentos[3],
            'desconto_percentual': 25,
            'preco_promocional': Decimal('135.00'),
            'data_inicio': hoje,
            'data_fim': hoje + timedelta(days=14),
        },
        {
            'nome': 'Peeling Químico Renovador',
            'descricao': 'Peeling químico com desconto especial para renovação completa da sua pele. Agende já!',
            'procedimento': procedimentos[1],
            'desconto_percentual': 15,
            'preco_promocional': Decimal('297.50'),
            'data_inicio': hoje - timedelta(days=2),
            'data_fim': hoje + timedelta(days=20),
        },
    ]

    for pd in promocoes_data:
        Promocao.objects.get_or_create(
            nome=pd['nome'],
            defaults={
                'descricao': pd['descricao'],
                'procedimento': pd['procedimento'],
                'desconto_percentual': pd['desconto_percentual'],
                'preco_promocional': pd['preco_promocional'],
                'data_inicio': pd['data_inicio'],
                'data_fim': pd['data_fim'],
                'ativa': True,
            }
        )

    print(f"  Promoções: {len(promocoes_data)} criadas/existentes")

    # ──────────────────────────────────────────
    # 9. PACOTES
    # ──────────────────────────────────────────
    pacote1, _ = Pacote.objects.get_or_create(
        nome='Pacote Facial Completo',
        defaults={
            'descricao': 'Limpeza de pele + Peeling Químico (4 sessões de cada)',
            'preco_total': Decimal('1900.00'),
            'ativo': True,
        }
    )
    pacote2, _ = Pacote.objects.get_or_create(
        nome='Pacote Bem-estar Corporal',
        defaults={
            'descricao': 'Drenagem Linfática + Massagem Relaxante (6 sessões de cada)',
            'preco_total': Decimal('1800.00'),
            'ativo': True,
        }
    )

    # Itens dos pacotes
    ItemPacote.objects.get_or_create(pacote=pacote1, procedimento=procedimentos[0], defaults={'quantidade_sessoes': 4})
    ItemPacote.objects.get_or_create(pacote=pacote1, procedimento=procedimentos[1], defaults={'quantidade_sessoes': 4})

    ItemPacote.objects.get_or_create(pacote=pacote2, procedimento=procedimentos[2], defaults={'quantidade_sessoes': 6})
    ItemPacote.objects.get_or_create(pacote=pacote2, procedimento=procedimentos[3], defaults={'quantidade_sessoes': 6})

    print("  Pacotes: 2 criados com itens")

    # ──────────────────────────────────────────
    # 10. PERGUNTAS DO PRONTUÁRIO
    # ──────────────────────────────────────────
    perguntas_data = [
        {'texto': 'Realizou algum tratamento estético anteriormente? Se sim, qual?', 'tipo_resposta': 'texto'},
        {'texto': 'Possui doença de pele (psoríase, vitiligo, dermatites, lúpus)?', 'tipo_resposta': 'boolean'},
        {'texto': 'Está em tratamento de câncer ou fez tratamento há menos de 5 anos?', 'tipo_resposta': 'boolean'},
        {'texto': 'Tem melasma ou pintas mais pigmentadas? Se sim, em qual local?', 'tipo_resposta': 'texto'},
        {'texto': 'Utiliza algum tipo de ácido na pele?', 'tipo_resposta': 'boolean'},
        {'texto': 'Toma alguma medicação contínua? Se sim, qual?', 'tipo_resposta': 'texto'},
        {'texto': 'Está grávida ou amamentando?', 'tipo_resposta': 'boolean'},
        {'texto': 'Tem alergia a algum medicamento ou alimento? Se sim, qual?', 'tipo_resposta': 'texto'},
        {'texto': 'Tem algum implante, marcapasso ou prótese de metal?', 'tipo_resposta': 'boolean'},
        {'texto': 'Tem tendência a queloides?', 'tipo_resposta': 'boolean'},
    ]

    for pq in perguntas_data:
        ProntuarioPergunta.objects.get_or_create(
            texto=pq['texto'],
            defaults={'tipo_resposta': pq['tipo_resposta'], 'ativa': True}
        )

    print(f"  Perguntas prontuário: {len(perguntas_data)} criadas/existentes")

    # ──────────────────────────────────────────
    # 11. VENDAS DE TESTE
    # ──────────────────────────────────────────
    vendas_data = [
        {'cliente': clientes[0], 'procedimento': procedimentos[0], 'profissional': prof1, 'valor': Decimal('180.00'), 'status': 'PAGO', 'dia_offset': -10},
        {'cliente': clientes[1], 'procedimento': procedimentos[1], 'profissional': prof2, 'valor': Decimal('350.00'), 'status': 'PAGO', 'dia_offset': -8},
        {'cliente': clientes[3], 'procedimento': procedimentos[2], 'profissional': prof3, 'valor': Decimal('180.00'), 'status': 'PENDENTE', 'dia_offset': -3},
        {'cliente': clientes[4], 'procedimento': procedimentos[3], 'profissional': prof3, 'valor': Decimal('180.00'), 'status': 'PAGO', 'dia_offset': -7},
        {'cliente': clientes[5], 'procedimento': procedimentos[0], 'profissional': prof1, 'valor': Decimal('180.00'), 'status': 'PENDENTE', 'dia_offset': -1},
    ]

    vendas_criadas = 0
    for vd in vendas_data:
        data_venda = hoje + timedelta(days=vd['dia_offset'])
        _, created = Venda.objects.get_or_create(
            cliente=vd['cliente'],
            procedimento=vd['procedimento'],
            data=data_venda,
            defaults={
                'profissional': vd['profissional'],
                'valor': vd['valor'],
                'status': vd['status'],
                'sessoes': 1,
            }
        )
        if created:
            vendas_criadas += 1

    print(f"  Vendas: {vendas_criadas} criadas")

    # ──────────────────────────────────────────
    # 12. ORÇAMENTOS DE TESTE
    # ──────────────────────────────────────────
    orcamentos_data = [
        {
            'nome_completo': 'Carolina Mendes', 'telefone': '(17) 99888-0001',
            'email': 'carolina@teste.com', 'procedimento': procedimentos[0],
            'valor': Decimal('180.00'), 'status': 'PENDENTE', 'sessoes': 1,
        },
        {
            'nome_completo': 'Daniela Freitas', 'telefone': '(17) 99888-0002',
            'email': 'daniela@teste.com', 'procedimento': procedimentos[2],
            'valor': Decimal('1080.00'), 'status': 'APROVADO', 'sessoes': 6,
        },
        {
            'nome_completo': 'Luiza Nascimento', 'telefone': '(17) 99888-0003',
            'email': 'luiza@teste.com', 'procedimento': procedimentos[3],
            'valor': Decimal('900.00'), 'status': 'PENDENTE', 'sessoes': 5,
        },
    ]

    orcamentos_criados = 0
    for od in orcamentos_data:
        _, created = Orcamento.objects.get_or_create(
            nome_completo=od['nome_completo'],
            procedimento=od['procedimento'],
            defaults={
                'telefone': od['telefone'],
                'email': od['email'],
                'valor': od['valor'],
                'status': od['status'],
                'sessoes': od['sessoes'],
                'data': hoje,
            }
        )
        if created:
            orcamentos_criados += 1

    print(f"  Orçamentos: {orcamentos_criados} criados")

    # ──────────────────────────────────────────
    # 13. CATEGORIAS DE PRODUTOS
    # ──────────────────────────────────────────
    categorias_data = [
        {'nome': 'Dermocosméticos', 'descricao': 'Produtos de cuidado com a pele profissionais'},
        {'nome': 'Injetáveis', 'descricao': 'Produtos injetáveis para procedimentos estéticos'},
        {'nome': 'Equipamentos', 'descricao': 'Equipamentos e acessórios para procedimentos'},
        {'nome': 'Consumíveis', 'descricao': 'Materiais de consumo para procedimentos'},
        {'nome': 'Higiene e Limpeza', 'descricao': 'Produtos de higienização e limpeza'},
    ]

    categorias = []
    for cd in categorias_data:
        cat, _ = CategoriaProduto.objects.get_or_create(
            nome=cd['nome'],
            defaults={'descricao': cd['descricao'], 'ativo': True}
        )
        categorias.append(cat)

    print(f"  Categorias: {len(categorias)} criadas/existentes")

    # ──────────────────────────────────────────
    # 14. PRODUTOS (Estoque)
    # ──────────────────────────────────────────
    produtos_data = [
        # Dermocosméticos
        {'nome': 'Sérum Vitamina C 30ml', 'categoria': categorias[0], 'marca': 'Adcos', 'preco_custo': Decimal('45.00'), 'preco_venda': Decimal('89.90'), 'quantidade_estoque': 25, 'estoque_minimo': 5, 'unidade': 'UN', 'data_validade': hoje + timedelta(days=180), 'lote': 'ADC-2025-001'},
        {'nome': 'Protetor Solar FPS 70', 'categoria': categorias[0], 'marca': 'La Roche-Posay', 'preco_custo': Decimal('38.00'), 'preco_venda': Decimal('79.90'), 'quantidade_estoque': 30, 'estoque_minimo': 10, 'unidade': 'UN', 'data_validade': hoje + timedelta(days=365), 'lote': 'LRP-2025-045'},
        {'nome': 'Ácido Hialurônico Tópico', 'categoria': categorias[0], 'marca': 'SkinCeuticals', 'preco_custo': Decimal('120.00'), 'preco_venda': Decimal('249.90'), 'quantidade_estoque': 15, 'estoque_minimo': 3, 'unidade': 'UN', 'data_validade': hoje + timedelta(days=20), 'lote': 'SKC-2025-112'},
        {'nome': 'Hidratante Facial Noturno', 'categoria': categorias[0], 'marca': 'Vichy', 'preco_custo': Decimal('55.00'), 'preco_venda': Decimal('119.90'), 'quantidade_estoque': 20, 'estoque_minimo': 5, 'unidade': 'UN', 'data_validade': hoje - timedelta(days=15), 'lote': 'VCH-2024-088'},
        {'nome': 'Água Micelar 400ml', 'categoria': categorias[0], 'marca': 'Bioderma', 'preco_custo': Decimal('42.00'), 'preco_venda': Decimal('89.90'), 'quantidade_estoque': 18, 'estoque_minimo': 5, 'unidade': 'UN', 'data_validade': hoje + timedelta(days=240), 'lote': 'BIO-2025-033'},
        # Injetáveis
        {'nome': 'Ácido Hialurônico 1ml', 'categoria': categorias[1], 'marca': 'Juvederm', 'preco_custo': Decimal('350.00'), 'preco_venda': Decimal('750.00'), 'quantidade_estoque': 10, 'estoque_minimo': 3, 'unidade': 'UN', 'data_validade': hoje + timedelta(days=15), 'lote': 'JUV-2025-007'},
        {'nome': 'Toxina Botulínica 100U', 'categoria': categorias[1], 'marca': 'Botox Allergan', 'preco_custo': Decimal('450.00'), 'preco_venda': Decimal('900.00'), 'quantidade_estoque': 8, 'estoque_minimo': 2, 'unidade': 'UN', 'data_validade': hoje - timedelta(days=5), 'lote': 'BOT-2024-199'},
        {'nome': 'Bioestimulador PLLA', 'categoria': categorias[1], 'marca': 'Sculptra', 'preco_custo': Decimal('600.00'), 'preco_venda': Decimal('1200.00'), 'quantidade_estoque': 5, 'estoque_minimo': 2, 'unidade': 'UN', 'data_validade': hoje + timedelta(days=90), 'lote': 'SCP-2025-021'},
        {'nome': 'Skinbooster 1ml', 'categoria': categorias[1], 'marca': 'Restylane', 'preco_custo': Decimal('280.00'), 'preco_venda': Decimal('600.00'), 'quantidade_estoque': 12, 'estoque_minimo': 3, 'unidade': 'UN', 'data_validade': hoje + timedelta(days=120), 'lote': 'RST-2025-055'},
        # Equipamentos (sem validade)
        {'nome': 'Ponteira Criolipólise P', 'categoria': categorias[2], 'marca': 'HTM', 'preco_custo': Decimal('200.00'), 'preco_venda': Decimal('0.00'), 'quantidade_estoque': 4, 'estoque_minimo': 2, 'unidade': 'UN'},
        {'nome': 'Agulha Microagulhamento 36P', 'categoria': categorias[2], 'marca': 'Dr. Pen', 'preco_custo': Decimal('15.00'), 'preco_venda': Decimal('0.00'), 'quantidade_estoque': 50, 'estoque_minimo': 20, 'unidade': 'UN', 'lote': 'DRP-2025-300'},
        # Consumíveis
        {'nome': 'Luvas Nitrílicas (cx 100)', 'categoria': categorias[3], 'marca': 'Supermax', 'preco_custo': Decimal('28.00'), 'preco_venda': Decimal('0.00'), 'quantidade_estoque': 15, 'estoque_minimo': 5, 'unidade': 'CX', 'data_validade': hoje + timedelta(days=730), 'lote': 'SMX-2025-150'},
        {'nome': 'Gaze Estéril (pct 500)', 'categoria': categorias[3], 'marca': 'Cremer', 'preco_custo': Decimal('22.00'), 'preco_venda': Decimal('0.00'), 'quantidade_estoque': 10, 'estoque_minimo': 3, 'unidade': 'PCT', 'data_validade': hoje + timedelta(days=25), 'lote': 'CRM-2025-078'},
        {'nome': 'Máscara Descartável (cx 50)', 'categoria': categorias[3], 'marca': 'Medix', 'preco_custo': Decimal('12.00'), 'preco_venda': Decimal('0.00'), 'quantidade_estoque': 20, 'estoque_minimo': 5, 'unidade': 'CX', 'data_validade': hoje + timedelta(days=500), 'lote': 'MDX-2025-200'},
        {'nome': 'Papel Lençol (rolo)', 'categoria': categorias[3], 'marca': 'Kolplast', 'preco_custo': Decimal('18.00'), 'preco_venda': Decimal('0.00'), 'quantidade_estoque': 12, 'estoque_minimo': 4, 'unidade': 'RL'},
        # Higiene
        {'nome': 'Álcool 70% (1L)', 'categoria': categorias[4], 'marca': 'Start', 'preco_custo': Decimal('8.00'), 'preco_venda': Decimal('0.00'), 'quantidade_estoque': 25, 'estoque_minimo': 10, 'unidade': 'UN', 'data_validade': hoje - timedelta(days=30), 'lote': 'STR-2024-400'},
        {'nome': 'Sabonete Antisséptico (500ml)', 'categoria': categorias[4], 'marca': 'Riocare', 'preco_custo': Decimal('15.00'), 'preco_venda': Decimal('0.00'), 'quantidade_estoque': 8, 'estoque_minimo': 3, 'unidade': 'UN', 'data_validade': hoje + timedelta(days=300), 'lote': 'RCR-2025-060'},
        {'nome': 'Clorexidina Alcoólica (100ml)', 'categoria': categorias[4], 'marca': 'Vic Pharma', 'preco_custo': Decimal('12.00'), 'preco_venda': Decimal('0.00'), 'quantidade_estoque': 15, 'estoque_minimo': 5, 'unidade': 'UN', 'data_validade': hoje + timedelta(days=10), 'lote': 'VPH-2025-018'},
    ]

    produtos = []
    for pd in produtos_data:
        defaults = {
            'categoria': pd['categoria'],
            'marca': pd['marca'],
            'preco_custo': pd['preco_custo'],
            'preco_venda': pd['preco_venda'],
            'quantidade_estoque': pd['quantidade_estoque'],
            'estoque_minimo': pd['estoque_minimo'],
            'unidade': pd['unidade'],
            'ativo': True,
        }
        if 'data_validade' in pd:
            defaults['data_validade'] = pd['data_validade']
        if 'lote' in pd:
            defaults['lote'] = pd['lote']

        produto, _ = Produto.objects.get_or_create(
            nome=pd['nome'],
            defaults=defaults,
        )
        produtos.append(produto)

    print(f"  Produtos: {len(produtos)} criados/existentes")

    # ──────────────────────────────────────────
    # 15. CONFIGURAÇÕES DO SISTEMA
    # ──────────────────────────────────────────
    configs_data = [
        {'chave': 'WHATSAPP_NUMERO', 'valor': '5517000000000', 'descricao': 'Número do WhatsApp da clínica'},
        {'chave': 'NOME_CLINICA', 'valor': 'Shiva Zen', 'descricao': 'Nome da clínica'},
        {'chave': 'HORARIO_FUNCIONAMENTO', 'valor': 'Seg-Sex: 9h-18h | Sáb: 9h-14h', 'descricao': 'Horário de funcionamento'},
        {'chave': 'ENDERECO', 'valor': 'Rua Example, 123 - Centro, Cidade/SP', 'descricao': 'Endereço da clínica'},
        {'chave': 'EMAIL_CONTATO', 'valor': 'contato@shivazen.com', 'descricao': 'Email de contato'},
        {'chave': 'INSTAGRAM', 'valor': '@shivazen', 'descricao': 'Instagram da clínica'},
        {'chave': 'WHATSAPP_BOT_ATIVO', 'valor': 'false', 'descricao': 'Ativar bot do WhatsApp'},
    ]

    for cfg in configs_data:
        ConfiguracaoSistema.objects.get_or_create(
            chave=cfg['chave'],
            defaults={'valor': cfg['valor'], 'descricao': cfg['descricao']}
        )

    print(f"  Configurações: {len(configs_data)} criadas/existentes")

    print("\nSeed concluído com sucesso!")
    print("=" * 50)
    print(f"  Profissionais:  {Profissional.objects.filter(ativo=True).count()}")
    print(f"  Procedimentos:  {Procedimento.objects.filter(ativo=True).count()}")
    print(f"  Clientes:       {Cliente.objects.filter(ativo=True).count()}")
    print(f"  Agendamentos:   {Atendimento.objects.count()}")
    print(f"  Promoções:      {Promocao.objects.filter(ativa=True).count()}")
    print(f"  Pacotes:        {Pacote.objects.count()}")
    print(f"  Vendas:         {Venda.objects.count()}")
    print(f"  Orçamentos:     {Orcamento.objects.count()}")
    print(f"  Produtos:       {Produto.objects.filter(ativo=True).count()}")
    print(f"  Categorias:     {CategoriaProduto.objects.filter(ativo=True).count()}")
    print("=" * 50)


if __name__ == '__main__':
    seed()
