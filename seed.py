import os
import django
from datetime import time, date, datetime, timedelta
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shivazen.settings')
django.setup()

from app_shivazen.models import (
    Profissional, Procedimento, ProfissionalProcedimento,
    DisponibilidadeProfissional, Cliente, Atendimento,
    Promocao, Preco, Pacote, ItemPacote, ProntuarioPergunta,
    Venda, Orcamento
)


def seed():
    print("Iniciando seed de dados de teste...")

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
    # 2. PROCEDIMENTOS (Faciais + Corporais)
    # ──────────────────────────────────────────
    procedimentos_data = [
        # Faciais
        {'nome': 'Limpeza de Pele Profunda', 'descricao': 'Remoção de impurezas, cravos e células mortas com técnicas especializadas. Inclui extração, tonificação e máscara hidratante.', 'duracao_minutos': 60},
        {'nome': 'Preenchimento Facial', 'descricao': 'Aplicação de ácido hialurônico para restaurar volume, contornos e suavizar linhas de expressão com resultado natural.', 'duracao_minutos': 45},
        {'nome': 'Harmonização Facial', 'descricao': 'Conjunto de procedimentos para equilibrar e realçar as características faciais com simetria e naturalidade.', 'duracao_minutos': 90},
        {'nome': 'Bioestimulador de Colágeno', 'descricao': 'Estímulo natural da produção de colágeno com ácido poli-L-láctico para firmeza e sustentação.', 'duracao_minutos': 60},
        {'nome': 'Toxina Botulínica (Botox)', 'descricao': 'Aplicação de toxina botulínica para suavizar rugas e linhas de expressão na testa, glabela e pés de galinha.', 'duracao_minutos': 30},
        {'nome': 'Peeling Químico', 'descricao': 'Renovação celular com ácidos específicos para eliminar manchas, cicatrizes de acne e uniformizar a textura.', 'duracao_minutos': 45},
        {'nome': 'Fototerapia LED', 'descricao': 'Terapia de luz não invasiva com diferentes comprimentos de onda para tratar acne, estimular colágeno e reduzir inflamações.', 'duracao_minutos': 30},
        {'nome': 'Microagulhamento', 'descricao': 'Estímulo da produção de colágeno com microagulhas para rejuvenescimento e redução de cicatrizes.', 'duracao_minutos': 60},
        {'nome': 'Skinbooster', 'descricao': 'Hidratação profunda com ácido hialurônico injetável para luminosidade e viço da pele facial.', 'duracao_minutos': 45},
        {'nome': 'Laser Fracionado', 'descricao': 'Tratamento a laser para cicatrizes, manchas e rejuvenescimento profundo da pele facial.', 'duracao_minutos': 45},
        # Corporais
        {'nome': 'Massagem Modeladora', 'descricao': 'Técnica com manobras firmes para redução de medidas, melhora da circulação e diminuição da celulite.', 'duracao_minutos': 60},
        {'nome': 'Radiofrequência Corporal', 'descricao': 'Aquecimento profundo dos tecidos para combater flacidez, celulite e gordura localizada de forma indolor.', 'duracao_minutos': 50},
        {'nome': 'Drenagem Linfática', 'descricao': 'Técnica manual para estimular o sistema linfático, reduzir inchaço e eliminar toxinas do corpo.', 'duracao_minutos': 60},
        {'nome': 'Criolipólise', 'descricao': 'Eliminação de gordura localizada por congelamento controlado, sem cirurgia e com resultados definitivos.', 'duracao_minutos': 60},
        {'nome': 'Lipocavitação', 'descricao': 'Ultrassom de alta potência que rompe células de gordura de forma não invasiva para modelagem corporal.', 'duracao_minutos': 45},
        {'nome': 'Tratamento de Estrias', 'descricao': 'Combinação de microagulhamento e ativos para minimizar estrias antigas e recentes.', 'duracao_minutos': 50},
        {'nome': 'Esfoliação Corporal', 'descricao': 'Renovação da pele com esfoliantes profissionais para hidratação, luminosidade e maciez.', 'duracao_minutos': 40},
        {'nome': 'Massagem Relaxante', 'descricao': 'Técnicas para alívio do estresse, tensão muscular e promoção do bem-estar integral.', 'duracao_minutos': 60},
        {'nome': 'Pós-operatório Estético', 'descricao': 'Protocolos para recuperação de cirurgias plásticas, minimizando edema, fibrose e desconfortos.', 'duracao_minutos': 60},
        {'nome': 'Estética Íntima', 'descricao': 'Procedimentos especializados para o bem-estar e saúde íntima com total discrição e profissionalismo.', 'duracao_minutos': 45},
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

    # Dra. Amanda: procedimentos faciais (primeiros 10)
    for proc in procedimentos[:10]:
        ProfissionalProcedimento.objects.get_or_create(profissional=prof2, procedimento=proc)

    # Camila: procedimentos corporais (últimos 10)
    for proc in procedimentos[10:]:
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
        'Preenchimento Facial': 1200,
        'Harmonização Facial': 2500,
        'Bioestimulador de Colágeno': 1800,
        'Toxina Botulínica (Botox)': 900,
        'Peeling Químico': 350,
        'Fototerapia LED': 150,
        'Microagulhamento': 450,
        'Skinbooster': 800,
        'Laser Fracionado': 600,
        'Massagem Modeladora': 200,
        'Radiofrequência Corporal': 250,
        'Drenagem Linfática': 180,
        'Criolipólise': 800,
        'Lipocavitação': 300,
        'Tratamento de Estrias': 400,
        'Esfoliação Corporal': 150,
        'Massagem Relaxante': 180,
        'Pós-operatório Estético': 250,
        'Estética Íntima': 500,
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
        {'cliente': clientes[1], 'profissional': prof1, 'procedimento': procedimentos[1], 'hora': time(10, 30), 'dia_offset': 0, 'status': 'CONFIRMADO'},
        {'cliente': clientes[2], 'profissional': prof2, 'procedimento': procedimentos[4], 'hora': time(14, 0), 'dia_offset': 0, 'status': 'AGENDADO'},
        # Amanhã
        {'cliente': clientes[3], 'profissional': prof1, 'procedimento': procedimentos[2], 'hora': time(9, 0), 'dia_offset': 1, 'status': 'AGENDADO'},
        {'cliente': clientes[4], 'profissional': prof2, 'procedimento': procedimentos[5], 'hora': time(11, 0), 'dia_offset': 1, 'status': 'AGENDADO'},
        {'cliente': clientes[5], 'profissional': prof3, 'procedimento': procedimentos[10], 'hora': time(14, 0), 'dia_offset': 1, 'status': 'CONFIRMADO'},
        # Próximos dias
        {'cliente': clientes[6], 'profissional': prof1, 'procedimento': procedimentos[3], 'hora': time(10, 0), 'dia_offset': 2, 'status': 'AGENDADO'},
        {'cliente': clientes[7], 'profissional': prof3, 'procedimento': procedimentos[12], 'hora': time(15, 0), 'dia_offset': 2, 'status': 'AGENDADO'},
        {'cliente': clientes[8], 'profissional': prof2, 'procedimento': procedimentos[7], 'hora': time(9, 0), 'dia_offset': 3, 'status': 'AGENDADO'},
        {'cliente': clientes[9], 'profissional': prof1, 'procedimento': procedimentos[8], 'hora': time(11, 0), 'dia_offset': 3, 'status': 'AGENDADO'},
        # Passados (concluídos/cancelados)
        {'cliente': clientes[0], 'profissional': prof1, 'procedimento': procedimentos[6], 'hora': time(10, 0), 'dia_offset': -2, 'status': 'CONCLUIDO'},
        {'cliente': clientes[1], 'profissional': prof2, 'procedimento': procedimentos[9], 'hora': time(14, 0), 'dia_offset': -3, 'status': 'CONCLUIDO'},
        {'cliente': clientes[2], 'profissional': prof3, 'procedimento': procedimentos[11], 'hora': time(9, 0), 'dia_offset': -1, 'status': 'CANCELADO'},
        {'cliente': clientes[3], 'profissional': prof1, 'procedimento': procedimentos[13], 'hora': time(16, 0), 'dia_offset': -5, 'status': 'CONCLUIDO'},
        {'cliente': clientes[4], 'profissional': prof3, 'procedimento': procedimentos[17], 'hora': time(11, 0), 'dia_offset': -7, 'status': 'CONCLUIDO'},
    ]

    agendamentos_criados = 0
    for ad in agendamentos_data:
        data_agend = hoje + timedelta(days=ad['dia_offset'])
        dt_inicio = datetime.combine(data_agend, ad['hora'])
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
            'nome': 'Combo Harmonização',
            'descricao': 'Pacote especial de harmonização facial com botox + preenchimento. Condições imperdíveis para transformar seu visual.',
            'procedimento': procedimentos[2],
            'desconto_percentual': 20,
            'preco_promocional': Decimal('2000.00'),
            'data_inicio': hoje - timedelta(days=3),
            'data_fim': hoje + timedelta(days=30),
        },
        {
            'nome': 'Massagem Relaxante - Semana do Bem-estar',
            'descricao': 'Na semana do bem-estar, massagem relaxante com valor especial. Cuide do seu corpo e mente.',
            'procedimento': procedimentos[17],
            'desconto_percentual': 25,
            'preco_promocional': Decimal('135.00'),
            'data_inicio': hoje,
            'data_fim': hoje + timedelta(days=14),
        },
        {
            'nome': 'Peeling + Fototerapia LED',
            'descricao': 'Combine peeling químico com fototerapia LED por um preço especial. Renovação completa da sua pele.',
            'procedimento': procedimentos[5],
            'desconto_percentual': 15,
            'preco_promocional': Decimal('297.50'),
            'data_inicio': hoje - timedelta(days=2),
            'data_fim': hoje + timedelta(days=20),
        },
        {
            'nome': 'Criolipólise - Verão Sem Gordura',
            'descricao': 'Elimine a gordura localizada com criolipólise e chegue ao verão em forma. Resultados duradouros.',
            'procedimento': procedimentos[13],
            'desconto_percentual': 10,
            'preco_promocional': Decimal('720.00'),
            'data_inicio': hoje,
            'data_fim': hoje + timedelta(days=45),
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
            'descricao': 'Limpeza de pele + Peeling + Fototerapia (4 sessões de cada)',
            'preco_total': Decimal('2200.00'),
            'ativo': True,
        }
    )
    pacote2, _ = Pacote.objects.get_or_create(
        nome='Pacote Modelagem Corporal',
        defaults={
            'descricao': 'Massagem modeladora + Radiofrequência + Drenagem (8 sessões de cada)',
            'preco_total': Decimal('4500.00'),
            'ativo': True,
        }
    )
    pacote3, _ = Pacote.objects.get_or_create(
        nome='Pacote Bem-estar',
        defaults={
            'descricao': 'Massagem relaxante + Esfoliação corporal (6 sessões de cada)',
            'preco_total': Decimal('1800.00'),
            'ativo': True,
        }
    )

    # Itens dos pacotes
    ItemPacote.objects.get_or_create(pacote=pacote1, procedimento=procedimentos[0], defaults={'quantidade_sessoes': 4})
    ItemPacote.objects.get_or_create(pacote=pacote1, procedimento=procedimentos[5], defaults={'quantidade_sessoes': 4})
    ItemPacote.objects.get_or_create(pacote=pacote1, procedimento=procedimentos[6], defaults={'quantidade_sessoes': 4})

    ItemPacote.objects.get_or_create(pacote=pacote2, procedimento=procedimentos[10], defaults={'quantidade_sessoes': 8})
    ItemPacote.objects.get_or_create(pacote=pacote2, procedimento=procedimentos[11], defaults={'quantidade_sessoes': 8})
    ItemPacote.objects.get_or_create(pacote=pacote2, procedimento=procedimentos[12], defaults={'quantidade_sessoes': 8})

    ItemPacote.objects.get_or_create(pacote=pacote3, procedimento=procedimentos[17], defaults={'quantidade_sessoes': 6})
    ItemPacote.objects.get_or_create(pacote=pacote3, procedimento=procedimentos[16], defaults={'quantidade_sessoes': 6})

    print("  Pacotes: 3 criados com itens")

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
        {'cliente': clientes[1], 'procedimento': procedimentos[4], 'profissional': prof2, 'valor': Decimal('900.00'), 'status': 'PAGO', 'dia_offset': -8},
        {'cliente': clientes[3], 'procedimento': procedimentos[2], 'profissional': prof1, 'valor': Decimal('2500.00'), 'status': 'PENDENTE', 'dia_offset': -3},
        {'cliente': clientes[4], 'procedimento': procedimentos[17], 'profissional': prof3, 'valor': Decimal('180.00'), 'status': 'PAGO', 'dia_offset': -7},
        {'cliente': clientes[5], 'procedimento': procedimentos[13], 'profissional': prof1, 'valor': Decimal('720.00'), 'status': 'PENDENTE', 'dia_offset': -1},
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
            'email': 'carolina@teste.com', 'procedimento': procedimentos[2],
            'valor': Decimal('2500.00'), 'status': 'PENDENTE', 'sessoes': 1,
        },
        {
            'nome_completo': 'Daniela Freitas', 'telefone': '(17) 99888-0002',
            'email': 'daniela@teste.com', 'procedimento': procedimentos[13],
            'valor': Decimal('800.00'), 'status': 'APROVADO', 'sessoes': 1,
        },
        {
            'nome_completo': 'Luiza Nascimento', 'telefone': '(17) 99888-0003',
            'email': 'luiza@teste.com', 'procedimento': procedimentos[10],
            'valor': Decimal('1600.00'), 'status': 'PENDENTE', 'sessoes': 8,
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

    print("\nSeed concluído com sucesso!")
    print("=" * 50)
    print(f"  Profissionais:  {Profissional.objects.filter(ativo=True).count()}")
    print(f"  Procedimentos:  {Procedimento.objects.filter(ativo=True).count()}")
    print(f"  Clientes:       {Cliente.objects.filter(ativo=True).count()}")
    print(f"  Agendamentos:   {Atendimento.objects.count()}")
    print(f"  Promoções:      {Promocao.objects.filter(ativa=True).count()}")
    print(f"  Pacotes:        {Pacote.objects.filter(ativo=True).count()}")
    print(f"  Vendas:         {Venda.objects.count()}")
    print(f"  Orçamentos:     {Orcamento.objects.count()}")
    print("=" * 50)


if __name__ == '__main__':
    seed()
