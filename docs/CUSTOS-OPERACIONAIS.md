# Custo operacional do sistema

> Quanto custa por mês manter o sistema da clínica no ar. Arquivo único (sobrescrever a cada
> revisão). Preços consultados em **30/10/2026** (fontes no fim). Revise uma vez por ano ou quando um
> provedor mudar a tabela. Integrações: [`PROJECT.md`](PROJECT.md) §7; deploy: §13–§14.

## 1. Premissas

- **Câmbio**: US$ 1 = R$ 5,10. A PTAX de 16/10/2026 foi de R$ 5,08, arredondada para cima.
- **IOF de 3,5%** sobre compra internacional no cartão. O custo efetivo fica em
  **R$ 5,28 por dólar** e é o valor usado em toda conversão deste documento.
- **Hospedagem**: plano Hobby do Railway, com um ambiente de produção (web + PostgreSQL 18) e um
  serviço Cron a cada 15 min.
- **Ambiente de testes**: o ambiente publicado da branch `dev` fica **desligado** fora dos ensaios (ver "Custos que não são mensais").
- **Volume**: estimado por atendimento realizado no mês, a partir das regras de comunicação do sistema
  ([`REGRAS-DE-NEGOCIO.md`](REGRAS-DE-NEGOCIO.md) §23 e §24):

| Mensagem por atendimento | Estimativa | Por quê |
|---|---|---|
| SMS (código OTP) | 1,0 | um código por pedido online e por acesso à área da cliente sem sessão; reenvio limitado |
| WhatsApp de utilidade | 1,25 | lembrete D-1 de quem autorizou (~0,8) + NPS no máximo a cada 90 dias por cliente (~0,4) + aviso de vaga da lista de espera (~0,05) |
| E-mail transacional | 1,6 | confirmação, termo, lembrete D-1 de quem não autorizou WhatsApp, reset de senha e convites da equipe |
| E-mail de marketing | por campanha | promoção disparada pelo painel só para quem consentiu + felicitação de aniversário |

- Os preços são de **lista**. Os valores de infraestrutura são **estimativas de consumo**, não
  medições: com o trial vencido, os serviços estão parados e o Railway não tem métricas recentes. O
  banco de demonstração ocupa 0,12 GB.

## 2. Itens de custo

| Item | Provedor | Como cobra | Preço de lista | Situação no sistema |
|---|---|---|---|---|
| Hospedagem web + banco + cron | Railway (Hobby) | US$ 5/mês com US$ 5 de uso incluídos; o excedente é cobrado por segundo | vCPU ≈ US$ 20/mês, RAM ≈ US$ 10/GB-mês, volume US$ 0,15/GB-mês, saída US$ 0,05/GB | 1 réplica, gunicorn com 1 worker e 4 threads; cron roda e desliga |
| Domínio `.com.br` | Registro.br | anual | R$ 40/ano | DNS do próprio Registro.br, sem custo |
| SMS (OTP) | Zenvia | pacote mensal | R$ 100 (até 1.000 SMS, R$ 0,10 cada); R$ 250 (até 2.942, R$ 0,085) | só OTP; cotas de 3/h por telefone, 10/h por IP e 60/h no total |
| WhatsApp | Meta (Cloud API direta) | por mensagem entregue | utilidade R$ 0,035; marketing R$ 0,3217; resposta de atendimento: 1.000/mês grátis | só templates de **utilidade** (lembrete, NPS, vaga) e só com consentimento; marketing não sai por WhatsApp |
| E-mail | Resend (ou Brevo, Mailgun, SendGrid, Postmark pelo anymail) | plano mensal | Resend Free: 3.000/mês, até 100/dia; Pro: US$ 20 (50 mil/mês, sem limite diário). Brevo Free: 300/dia | o Hobby do Railway bloqueia SMTP; o e-mail sai por API HTTP |
| Backup externo | Cloudflare R2 | por GB-mês | 10 GB-mês grátis; depois US$ 0,015/GB | dump cifrado (age) diário; 30 diários + 12 mensais cabem no gratuito |
| Erros | Sentry (Developer) | plano | grátis: 5 mil erros/mês, 1 usuário | filtros de dado pessoal em `utils/pii` |
| Monitor dos jobs | healthchecks.io (Hobbyist) | plano | grátis: 20 checks | `CRON_MONITOR_URL` |
| Antirrobô | Cloudflare Turnstile | — | grátis | formulários públicos e pedido de SMS |
| Notificação da equipe | Web Push (VAPID) | — | grátis (serviço do navegador) | novo agendamento, cancelamento, sem desfecho |
| Agenda da profissional | feed ICS | — | grátis | sem API do Google Agenda |
| Mapa | link do Google Maps | — | grátis | sem mapa embutido, que exigiria chave paga |
| Fontes e bibliotecas | hospedadas no próprio site | — | grátis | sem Google Fonts nem CDN |

## 3. Estimativa da hospedagem (Railway)

| Serviço | Início (150 atend./mês) | Rotina (400) | Agenda cheia (800) |
|---|---|---|---|
| Web (RAM / CPU média) | 0,30 GB / 0,03 vCPU → US$ 3,60 | 0,35 GB / 0,05 vCPU → US$ 4,50 | 2 workers: 0,60 GB / 0,10 vCPU → US$ 8,00 |
| PostgreSQL (RAM / CPU / disco) | 0,20 GB / 0,01 / 1 GB → US$ 2,35 | 0,25 GB / 0,02 / 2 GB → US$ 3,20 | 0,40 GB / 0,05 / 3 GB → US$ 5,45 |
| Cron (96 execuções/dia, ~20 s cada) | US$ 0,35 | US$ 0,40 | US$ 0,50 |
| Saída de dados | US$ 0,10 | US$ 0,25 | US$ 0,50 |
| **Fatura (mín. US$ 5)** | **≈ US$ 6,50 → R$ 34** | **≈ US$ 8,50 → R$ 45** | **≈ US$ 14,50 → R$ 77** |

O consumo fica baixo por decisões do próprio sistema:

- Páginas renderizadas no servidor, sem SPA.
- Um processo só, sem Redis nem Celery (os jobs rodam pelo serviço Cron, que liga, executa e
  desliga).
- `statement_timeout` de 30 s.
- Testes que reprovam consultas N+1 no painel.

Para ver o consumo real depois do go-live, use o painel **Usage** do Railway. Com ele, refaça esta
tabela no primeiro mês.

## 4. Custo mensal por cenário

| Item | Início (150) | Rotina (400) | Agenda cheia (800) |
|---|---|---|---|
| Railway | R$ 34,00 | R$ 45,00 | R$ 77,00 |
| Domínio (R$ 40 ÷ 12) | R$ 3,33 | R$ 3,33 | R$ 3,33 |
| SMS Zenvia (150 / 400 / 800 SMS) | R$ 100,00 | R$ 100,00 | R$ 100,00 |
| WhatsApp utilidade (190 / 500 / 1.000) | R$ 6,65 | R$ 17,50 | R$ 35,00 |
| E-mail (ver "E-mail: qual plano") | R$ 0,00 | R$ 105,60 | R$ 105,60 |
| Backup, Sentry, healthchecks, Turnstile, push | R$ 0,00 | R$ 0,00 | R$ 0,00 |
| **Total por mês** | **≈ R$ 144** | **≈ R$ 271** | **≈ R$ 321** |
| Sem plano pago de e-mail (campanha de até 300 envios/dia no Brevo Free) | ≈ R$ 144 | ≈ R$ 166 | ≈ R$ 215 |
| **Por atendimento** | R$ 0,96 | R$ 0,68 (R$ 0,41) | R$ 0,40 (R$ 0,27) |
| Por ano | ≈ R$ 1.730 | ≈ R$ 3.260 | ≈ R$ 3.850 |

**Leitura:**

- **O SMS é o maior custo fixo.** O pacote mínimo de R$ 100 vale até 1.000 códigos e, nos três
  cenários, ficaria subutilizado.
- **O e-mail pago só é preciso quando há campanha.** O Resend Free (100/dia) cobre os transacionais;
  uma promoção para uma base de 400 a 1.500 clientes que consentiram estoura o limite diário.
- **O custo cai por atendimento à medida que a agenda enche**, porque quase tudo é fixo.
- **No ticket da clínica, o sistema custa menos de 1% de um atendimento.** O catálogo vai de R$ 60
  (drenagem facial) a R$ 2.300; num atendimento de R$ 150, R$ 0,68 equivale a 0,45%. Evitar **uma**
  falta de R$ 300 por mês (o lembrete D-1 existe para isso) já paga a operação do cenário Rotina.

## 5. E-mail: qual plano

| Situação | Plano | Custo |
|---|---|---|
| Só transacionais (até ~100/dia) | Resend Free | R$ 0 |
| Campanhas para até ~300 clientes por dia | Brevo Free (300/dia) | R$ 0 — confirmar se o plano gratuito põe marca do Brevo no e-mail |
| Campanhas maiores ou mais de uma por semana | Resend Pro (50 mil/mês, sem limite diário) | US$ 20 → R$ 105,60 |

A troca de provedor é só de configuração: `EMAIL_BACKEND=anymail.backends.<provedor>.EmailBackend` e
a chave do provedor na env (`PROJECT.md` §14 i). A fila de e-mail reenvia o que falhou a cada 15 min.

## 6. Custos que não são mensais

- **Na implantação (uma vez):**
  - Domínio no 1º ano: R$ 40.
  - Número de celular dedicado ao WhatsApp da API (chip pré-pago). Esse número não pode estar em uso
    no aplicativo WhatsApp comum.
  - Verificação do negócio na Meta: sem custo, mas exige CNPJ.
  - Contas no Railway, Sentry, Cloudflare e healthchecks: sem custo. O Railway pede cartão.
  - **Sem** a taxa de ativação de R$ 649 da Zenvia. Ela só existe para WhatsApp/RCS pela Zenvia, e o
    sistema fala com a Meta direto.
- **Ambiente de testes publicado** (branch `dev`): enquanto ligado, custa quase o mesmo que a produção
  (outro web + outro Postgres, ≈ US$ 5 a 6/mês a mais). Ligue só para ensaiar uma migration ou o
  go-live (`PROJECT.md` §13.1) e desligue depois.
- **Plano Pro do Railway** (US$ 20/mês com US$ 20 de uso, ≈ R$ 106): só se a clínica precisar de mais
  de uma pessoa na conta ou de suporte do Railway. O consumo estimado cabe no crédito incluído.

## 7. Travas de custo no sistema

Cada canal pago tem um teto no código. Abuso ou erro não vira fatura:

| Risco | Trava | Onde |
|---|---|---|
| Robô pedindo SMS em massa (*SMS pumping*) | Turnstile + 3 SMS/h por telefone, 10/h por IP e **60/h no site todo**: o pior caso fica em ~R$ 6/h e para no teto | `utils/sms`, env `SMS_MAX_*` |
| Reenvio de código em sequência | 60 s entre reenvios; código vale 10 min | `OTP_REENVIO_MINIMO_SEG` |
| Spam pelo formulário de contato | 5/min e 20/dia por IP; teto de 50/dia no site | `views/publico/contato` |
| NPS repetido | no máximo 1 pesquisa a cada 90 dias por cliente, nunca em retorno | `constants.NPS_INTERVALO_DIAS` |
| Marketing caro por WhatsApp | promoção só por e-mail; WhatsApp só em template de utilidade, ~9× mais barato que o de marketing | `services/comunicacao`, `services/marketing` |
| Lembrete para quem não aceitou WhatsApp | vai por e-mail (custo zero) | `services/lembretes` |
| Envio sem provedor configurado | falha fechada: nada sai, nada é cobrado nem marcado como enviado | `utils/sms`, `utils/whatsapp` |
| Avisos à equipe | Web Push gratuito em vez de SMS ou WhatsApp | `services/push` |

## 8. Esforço de operação (tempo, não dinheiro)

| Frequência | Tarefa | Tempo |
|---|---|---|
| Semanal | Olhar o Sentry e o healthchecks (job atrasado, erro novo) | ~15 min |
| Mensal | Testar a restauração do backup (`scripts/backup/testar_restauracao.sh`) | ~30 min |
| Mensal | Conferir as faturas (Railway, Meta, Zenvia, e-mail) e o saldo do pacote de SMS | ~15 min |
| Anual | Renovar o domínio e revisar este documento com os preços do ano | ~1 h |
| Quando mudar o texto | Reaprovar o template do WhatsApp na Meta | ~1 dia de espera |

## 9. Fontes (consultadas em 30/10/2026)

- Railway, planos e tarifas: https://railway.com/pricing
- Meta, modelo de cobrança do WhatsApp Business Platform: https://developers.facebook.com/docs/whatsapp/pricing
- Tarifas do Brasil, em vigor desde 01/10/2026 (tabela da Meta reproduzida por terceiro):
  https://zynfo.ai/whatsapp-pricing/brazil
- Zenvia, pacotes de SMS: https://zenvia.com/zoe/
- Resend, planos: https://resend.com/pricing
- Brevo, plano gratuito de 300 e-mails/dia (resenha de terceiro): https://www.sender.net/reviews/brevo/pricing/free-plan
- Registro.br, valor de R$ 40/ano: https://www.nuvemshop.com.br/blog/registro-br
- Sentry, plano Developer gratuito: https://costbench.com/software/developer-tools/sentry/free-plan/
- healthchecks.io, planos: https://healthchecks.io/pricing/
- Cloudflare R2, cota gratuita: https://developers.cloudflare.com/r2/pricing
- Câmbio (PTAX de 16/10/2026): https://www.simianx.ai/pt/stories/us-dollar-exchange-rate-by-year-every-year-1971-2026-table
- Cotação oficial: https://www.bcb.gov.br/estabilidadefinanceira/historicocotacoes

Algumas tarifas vieram de páginas de terceiros. Antes de contratar, confira o valor na página do
próprio provedor.
