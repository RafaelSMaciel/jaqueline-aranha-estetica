# Design e marketing: escolhas e porquês

> Por que o sistema tem a cara que tem: cores, tipografia, tom de voz, acessibilidade e os recursos
> de marketing. Arquivo único (sobrescrever a cada revisão).
>
> - Implementação técnica do design system (tokens, camadas de CSS, componentes):
>   [`specs/fundacao-front-design.md`](specs/fundacao-front-design.md).
> - Decisão D4: [`ARCHITECTURE.md`](ARCHITECTURE.md).
> - Custo de cada canal: [`CUSTOS-OPERACIONAIS.md`](CUSTOS-OPERACIONAIS.md).

## 1. Posicionamento

A Jaqueline Aranha Estética é uma clínica de **estética e bem-estar** em São José do Rio Preto/SP. O
catálogo vai da massagem relaxante e da drenagem a injetáveis (toxina botulínica, preenchimentos,
bioestimulador).

A identidade precisa resolver uma tensão:

- **Confiança técnica**, porque são procedimentos no rosto e no corpo.
- **Acolhimento e prazer**, porque é cuidado pessoal, não tratamento de doença.

A direção visual escolhida, **"Clínico-premium"** (decisão D4), fica no meio do caminho:

- **Premium:** o dourado e a tipografia serifada passam valor e cuidado.
- **Sóbrio:** os neutros claros e o texto de alto contraste passam limpeza e seriedade.
- **Nunca hospitalar:** nada de branco-gelo com azul ou verde cirúrgico, nada de cruz nem de
  linguagem de consultório.

O site é um **funil para o agendamento**. Cada página leva a "Agendar avaliação" ou ao WhatsApp da
clínica.

## 2. Paleta e psicologia das cores

A cor é o primeiro sinal de personalidade de uma marca. Matiz, saturação e luminosidade mudam a
personalidade percebida (sinceridade, entusiasmo, competência, sofisticação, robustez), como mostram
Labrecque e Milne (2012). Os significados das cores são culturais e dependem do contexto (Heller,
2013): por isso a paleta foi escolhida pelo **conjunto** e pelo uso, não por uma cor isolada.

| Papel | Cor | Onde aparece | Por quê |
|---|---|---|---|
| **Marca** | Dourado `#C9A84C` | botões principais, ícones, fios decorativos, cor do tema do PWA | Ver "Por que dourado", logo abaixo. |
| Dourado para texto | `#7A5F1F` (claro) / `#C9A84C` (escuro) | links, títulos em destaque, anel de foco | O `#C9A84C` como texto no fundo claro dá só 2,2:1. O tom escurecido dá 5,8:1 (AA) e continua lido como "dourado". |
| Texto sobre o dourado | `#23200F` (café quase preto) | rótulo dos botões principais | 7,2:1 sobre o dourado. Mais quente que o preto puro e sem o brilho do branco sobre o amarelo. |
| Fundo | `#FAFAF8` (off-white quente) | página | O branco puro (`#FFFFFF`) com luz fria é a cor do hospital. O off-white levemente quente lembra papel, linho e toalha de spa. |
| Superfície | `#FFFFFF` | cartões | O cartão se destaca do fundo sem precisar de borda forte. |
| Areia | `#F1EFE8` | realces, faixas alternadas | Respiro entre seções, no registro "natural/terra". |
| Texto | Grafite `#1B1F23` | corpo | 15,9:1 no fundo. Menos duro que o preto absoluto em leitura longa. |
| Texto suave | Taupe `#6B6258` | legendas, metadados | Cinza **quente** (puxado ao marrom) em vez de cinza azulado: conversa com o dourado. 5,7:1 (AA). |
| Menu da equipe | Café `#1F1A14` | barra lateral do painel e do portal | Substituiu o **azul-marinho** do template comprado. O azul passa "corporativo/sistema" e não tem relação com a marca. O café com destaque dourado (`#E3C674`, 7,9:1) faz o painel parecer da mesma casa que o site. |
| Rodapé | `#16181C` | rodapé do site | Fecha a página com peso visual e faz o dourado do logo brilhar. |
| Ação do WhatsApp | Verde `#1D7A4F` | botão flutuante e CTAs de WhatsApp | O verde é reconhecido como "WhatsApp/seguir". O verde oficial `#25D366` com texto branco dá 2:1 (ilegível); o tom fechado dá 5,3:1. |
| Erro | Vermelho tijolo `#A32D2D` | mensagens de erro | Sério sem ser alarmante. Nada de vermelho de emergência. |

### 2.1 Por que dourado

- **Valor e cuidado especial.** O ouro é associado a valor, celebração e luxo (Heller, 2013). Ele
  passa a ideia de "momento de cuidado consigo" que a estética vende, e é a linguagem visual de
  cosméticos e spas de categoria superior.
- **Ouro velho, não amarelo.** O tom é **dessaturado e terroso**, não o amarelo-limão nem o
  metalizado brilhante. Em excesso, o dourado vira ostentação. Por isso ele é **acento** (botão,
  ícone, fio) e nunca fundo de grandes áreas.
- **Calor sem infantilizar.** O dourado é quente como o rosa e o pêssego, mais comuns em estética,
  mas não infantiliza a marca nem restringe o público.
- **Diferenciação.** Clínicas de estética da região tendem ao rosa, ao lilás ou ao azul-clínico. O
  dourado com café se destaca sem fugir do universo da beleza.
- **Decisão do dono.** O dono da clínica confirmou o `#C9A84C` como cor final da marca.

### 2.2 Cores que foram descartadas

| Opção | Por que não |
|---|---|
| Azul + branco frio | Lido como hospital, laboratório ou plano de saúde: o contrário de "bem-estar". |
| Verde-água / menta | Mesmo problema (centro cirúrgico, farmácia). Fica só como verde de ação do WhatsApp. |
| Rosa / lilás dominante | Clichê do segmento, pouca diferenciação e tom infantil para procedimentos injetáveis. |
| Preto + dourado brilhante | Luxo "noturno" e distante (joalheria, balada). Afasta quem busca acolhimento. |
| Azul-marinho no painel | Herança do template comprado, sem relação com a marca. Trocado pelo café (tabela da paleta). |

### 2.3 Cores de status

A agenda usa cores de status por convenção, para leitura rápida pela equipe:

| Status | Cor |
|---|---|
| Pendente | âmbar |
| Agendado | cinza |
| Confirmado | azul |
| Realizado | verde |
| Cancelado | vermelho |
| Faltou | laranja |
| Reagendado | roxo |

- **Sempre com o texto.** O status nunca depende só da cor (WCAG 1.4.1): o selo mostra o nome, para
  quem tem daltonismo.
- **Fundo claro + texto escuro** do mesmo matiz, com AA nos dois temas.

### 2.4 Tema escuro

- **Disponível nas duas pontas.** Site, painel e portal têm tema escuro: segue o sistema ou é escolhido
  no botão do cabeçalho, e a escolha fica num cookie.
- **Por quê:**
  - Conforto à noite: a cliente costuma agendar pelo celular fora do horário comercial.
  - Menos brilho para a equipe que passa o dia na agenda.
- **Como:**
  - Fundo grafite azulado `#14171A`, não preto puro.
  - Texto `#EDEFEC`.
  - O dourado da marca volta a ser cor de texto (7,9:1 sobre o fundo escuro).

## 3. Tipografia

| Uso | Fonte | Por quê |
|---|---|---|
| Títulos | **Playfair Display** (serifada, 500/600/700) | Serifa de alto contraste entre traços finos e grossos, no estilo didone das revistas de moda e beleza. Passa elegância e o lado "editorial/premium" da marca. |
| Texto e interface | **Lato** (sem serifa humanista, 400/700) | Formas abertas e arredondadas: legível em tela pequena e mais calorosa que as sem serifa geométricas ou técnicas. Boa nos números da agenda e das tabelas. |

- **Contraste entre as duas.** O par serifa + sem serifa separa o "tom de marca" dos títulos da
  "clareza de uso" do texto.
- **Hospedadas no próprio site** (decisão F9), sem Google Fonts:
  - nenhum IP de visitante vai para terceiro (LGPD);
  - nada a mais na política de segurança (CSP);
  - só o subconjunto latino e os pesos usados (página mais leve).
- **Escala das telas da equipe:**
  - texto base de 15 px;
  - rótulos de 13 px;
  - **nada abaixo de 12 px**;
  - espaçamento em múltiplos de 4 px;
  - cantos de 8 px (controle), 12 px (menu, aviso) e 16 px (cartão, janela).
- **Caixa de frase** em títulos e botões ("Agendar avaliação", "Perguntas frequentes"). É mais
  conversacional e mais fácil de ler que Tudo Em Maiúsculas.

## 4. Tom de voz e conteúdo

- **Estética, não hospital.** O template comprado veio com textos de consultório. Eles foram
  trocados:

| Antes (template) | Depois |
|---|---|
| paciente | cliente |
| consulta | avaliação / horário / atendimento |
| prontuário (no site) | histórico de cuidados |
| "pronto atendimento", "intercorrência" | acompanhamento pós-procedimento |

- **Caloroso e direto.** Frases curtas na segunda pessoa ("Escolha o melhor horário"). Erros dizem o
  que fazer ("Escolha a data e um dos horários livres"), sem códigos técnicos.
- **Ícones** de folha, flor e brilho, em traço fino, em vez de escudo, cadeado ou cruz médica.
- **O catálogo real** (serviços e preços informados pela clínica) substituiu o conteúdo genérico. O
  preço aparece no agendamento, por transparência.

## 5. Experiência e layout

- **Celular primeiro.** A cliente chega pelo Instagram, pelo WhatsApp ou pela busca no celular. Cada
  tela é desenhada primeiro em 375 px. O CI confere três larguras (celular, tablet e desktop), sem
  rolagem lateral.
- **Página inicial curta** (de 750 para 313 linhas na reforma):
  1. Herói com a chamada.
  2. Serviços.
  3. Equipe com "Agendar com…".
  4. Chamada final.
  5. Dúvidas frequentes.

  O botão "Agendar avaliação" aparece no topo e no fim da página.
- **Agendamento em etapas:** procedimento → data e horário → dados e código SMS → confirmação. Uma
  decisão por tela reduz a desistência, e cada etapa tem um botão "Continuar".
- **Botão flutuante do WhatsApp** em todas as páginas do site. É o canal de conversa preferido do
  público da clínica, e quem não quer agendar sozinho fala com uma pessoa.
- **Animação discreta** ao rolar (as seções surgem suavemente). Ela é desligada para quem pede menos
  movimento no sistema (`prefers-reduced-motion`).
- **Telas da equipe:**
  - um design system próprio: cartões, tabelas que viram lista no celular, agenda em grade;
  - o painel é um **PWA** (instalável no celular, com notificação de novo agendamento).

## 6. Acessibilidade como decisão de design

- **Meta: WCAG 2.1 nível AA** nos dois temas:
  - texto com 4,5:1;
  - borda de campo com 3:1 (`#8C857A`, 3,6:1);
  - foco do teclado sempre visível, no dourado escuro.
- **Teste em toda rota.** O CI roda o axe-core em todas as rotas do site e do painel
  (`tests/test_crawl_visual.py`) e reprova violações graves.
- **Formulários:** rótulo visível, ajuda e erro ligados ao campo, e mensagem anunciada ao leitor de
  tela.
- **Por que importa para o marketing:** acessibilidade amplia o público (pessoas idosas, baixa visão,
  daltonismo, uso com uma mão no celular) e melhora o SEO.

## 7. Recursos de marketing do sistema

| Recurso | O que faz | Por quê |
|---|---|---|
| **SEO local** | título e descrição por página, Open Graph, dados estruturados schema.org (`BeautySalon`/`HealthAndBeautyBusiness`, `Service` com `Offer`, `FAQPage`, `BreadcrumbList`), sitemap e robots; páginas privadas com `noindex` | Aparecer na busca "estética em Rio Preto" e no compartilhamento pelo WhatsApp com imagem e texto certos. |
| **Agendamento online 24 h** | a cliente escolhe o horário livre e confirma com um código SMS | Converte na hora em que a vontade surge, sem esperar resposta. |
| **Prova social honesta** | nota média só com **10+ avaliações**; "clientes atendidas" só a partir de **50** (arredondado para baixo); anos de experiência só se a clínica preencher | Número real gera confiança. Número inventado é publicidade enganosa (CDC, art. 37). |
| **Depoimentos** | só nota ≥ 9 no NPS, com autorização da cliente e aprovação da clínica | Depoimento verdadeiro, com consentimento (LGPD). |
| **NPS pós-atendimento** | pesquisa de 0 a 10 por WhatsApp, no máximo a cada 90 dias; nota ≤ 6 gera alerta para a equipe | Mede a satisfação e permite recuperar a cliente insatisfeita antes de uma avaliação pública ruim. |
| **Lembrete D-1** | WhatsApp ou e-mail na véspera, com confirmar ou remarcar | Reduz falta (horário vazio é receita perdida) e facilita remarcar em vez de sumir. |
| **Lista de espera** | inscrição por procedimento; aviso quando abre vaga | Recupera o horário de quem cancelou. |
| **Promoções** | página pública e e-mail disparado pelo painel, só para quem consentiu | Ativa a base atual (mais barato que conquistar cliente nova), dentro da LGPD. |
| **Aniversário** | e-mail de felicitação com consentimento | Relacionamento e lembrança da marca na data certa. |
| **Indicação com cashback** | a indicadora ganha **R$ 50** de crédito no 1º atendimento pago da indicada | Boca a boca, o canal mais forte em estética, com recompensa rastreável. |
| **Pacotes e retornos** | sessões com validade; retorno sem custo dentro da janela do procedimento | Fidelização e resultado completo do tratamento. |
| **Área da cliente** | ver, remarcar e cancelar horários, preferências de comunicação e pedidos LGPD | Autonomia e transparência geram confiança. |
| **Confiança e LGPD** | só cookies essenciais (sessão, segurança e tema), sem pixel de anúncio nem rastreador de terceiros; política de privacidade clara; descadastro em um clique | Privacidade é argumento de venda numa clínica que guarda dado de saúde. A medição fica no painel (ver "Métricas para acompanhar"), com dados próprios. |

**Matriz de canais pelo custo.** O canal de cada mensagem foi escolhido pelo custo
([`CUSTOS-OPERACIONAIS.md`](CUSTOS-OPERACIONAIS.md)):

- **Marketing vai por e-mail.** É gratuito ou barato. O template de marketing do WhatsApp custa
  R$ 0,32 por mensagem.
- **WhatsApp é só para utilidade** (lembrete, NPS, vaga), a R$ 0,035.
- **SMS é só para o código de acesso.**

## 8. Métricas para acompanhar

O painel mostra no dia a dia os números que dizem se o design e o marketing funcionam:

- atendimentos do dia e da semana e ocupação da agenda;
- receita do mês e ticket médio;
- clientes ativas nos últimos 90 dias;
- faltas no mês;
- resposta aos lembretes (confirmados, cancelados, sem resposta);
- NPS, com promotores, neutros e detratores;
- valor total por cliente (LTV), na ficha de cada uma.

A recomendação é comparar antes e depois de cada campanha ou mudança no site.

## 9. Referências

- HELLER, Eva. *A psicologia das cores: como as cores afetam a emoção e a razão*. São Paulo: Gustavo
  Gili, 2013.
- LABRECQUE, Lauren I.; MILNE, George R. Exciting red and competent blue: the importance of color in
  marketing. *Journal of the Academy of Marketing Science*, v. 40, n. 5, p. 711–727, 2012.
- W3C. *Web Content Accessibility Guidelines (WCAG) 2.1*. 2018. https://www.w3.org/TR/WCAG21/
- BRASIL. Lei nº 8.078, de 11 de setembro de 1990 (Código de Defesa do Consumidor), art. 37.
- BRASIL. Lei nº 13.709, de 14 de agosto de 2018 (Lei Geral de Proteção de Dados Pessoais).
