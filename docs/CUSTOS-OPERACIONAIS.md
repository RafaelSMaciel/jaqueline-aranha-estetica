# Custo operacional do sistema

> Quanto custa por mês manter o sistema da clínica no ar, com as ferramentas recomendadas. Arquivo
> único (sobrescrever a cada revisão). A clínica prevê **no máximo 60 atendimentos por mês**. Preços
> de lista consultados em **30/10/2026** (fontes no fim). A planilha
> [`tcc/ShivaZen-Custos-Operacionais.xlsx`](tcc/ShivaZen-Custos-Operacionais.xlsx) tem as mesmas
> contas em fórmulas, com as premissas editáveis. Ela é gerada por
> [`tcc/gerar_planilha_custos.py`](tcc/gerar_planilha_custos.py): mudou um preço aqui, mude lá.

## 1. Resumo

| | Valor |
|---|---|
| **Custo mensal** | **≈ R$ 50,11** |
| Por atendimento (60/mês) | ≈ R$ 0,84 |
| Por ano | ≈ R$ 601 |
| Implantação (uma vez) | ≈ R$ 96 |

Com as ferramentas que o código usa hoje, o mesmo mês custaria ≈ R$ 142,91. A diferença vem quase
toda do pacote mínimo de SMS da Zenvia (R$ 100/mês). A troca está em "No radar: mudança de código".

## 2. Premissas

- **Volume**: até 60 atendimentos por mês.
- **Mensagens por atendimento**, a partir das regras de comunicação do sistema
  ([`REGRAS-DE-NEGOCIO.md`](REGRAS-DE-NEGOCIO.md) §23 e §24):
  - 1 código de acesso por SMS (pedido online ou acesso à área da cliente);
  - 1,25 mensagem de WhatsApp: lembrete D-1 de quem autorizou (~0,8), pesquisa NPS no máximo a cada
    90 dias por cliente (~0,4) e aviso de vaga da lista de espera (~0,05);
  - ~1,6 e-mail transacional, mais as campanhas para quem consentiu.
- **Câmbio**: US$ 1 = R$ 5,10 (PTAX de 16/10/2026: R$ 5,08, arredondada para cima). Com o IOF de 3,5%
  da compra internacional no cartão, **R$ 5,28 por dólar**.
- **Hospedagem**: valores estimados de consumo, não medidos. Com o trial vencido, os serviços estão
  parados e o Railway não tem métricas recentes. O banco de demonstração ocupa 0,12 GB.

## 3. Ferramentas e custo mensal

Critério: menor custo fixo, sem servidor para administrar e sem pagar por capacidade que a clínica
não usa.

| Função | Ferramenta | Quantidade/mês | Preço | Custo/mês |
|---|---|---|---|---|
| Site, painel, banco e rotinas | Railway Hobby (PostgreSQL 18 incluído) | 1 | US$ 7 de uso estimado (a mensalidade de US$ 5 já inclui US$ 5) | R$ 36,95 |
| Domínio e DNS | Registro.br | 1 | R$ 40/ano | R$ 3,33 |
| Código de acesso da cliente | **SMS pré-pago sem mensalidade** (SMSGo, créditos que não expiram) | 60 | R$ 0,12 por SMS | R$ 7,20 |
| Lembrete D-1, NPS e aviso de vaga | WhatsApp Cloud API (Meta, direto, sem intermediário) | 75 | R$ 0,035 por mensagem | R$ 2,63 |
| E-mail (transacional e campanhas) | **Brevo Free** | — | 300 e-mails por dia sem custo | R$ 0,00 |
| Backup do banco | Cloudflare R2 (10 GB grátis) | — | — | R$ 0,00 |
| Erros da aplicação | Sentry Developer (5 mil erros/mês) | — | — | R$ 0,00 |
| Monitor das rotinas | healthchecks.io Hobbyist (20 checks) | — | — | R$ 0,00 |
| Antirrobô nos formulários | Cloudflare Turnstile | — | — | R$ 0,00 |
| Avisos à equipe | Web Push (VAPID) | — | — | R$ 0,00 |
| **Total** | | | | **R$ 50,11** |

- **Sem custo e sem fornecedor**: a agenda da profissional vai pelo feed ICS (sem API do Google
  Agenda); o mapa é um link do Google Maps (sem chave paga); fontes e bibliotecas ficam no próprio
  site.
- **WhatsApp**: só templates de utilidade e só com consentimento. Marketing não sai por WhatsApp:
  o template de marketing custa R$ 0,32 por mensagem, ~9 vezes o de utilidade. As respostas dentro da
  janela de atendimento têm 1.000 por mês grátis.
- **E-mail**: o Hobby do Railway bloqueia SMTP, então o e-mail sai por API HTTP (anymail). O Brevo
  Free comporta a campanha para até ~300 clientes num dia. O Resend Free comporta só 100.

## 4. Hospedagem (estimativa do Railway)

| Serviço | Consumo médio | US$/mês |
|---|---|---|
| Web (gunicorn, 1 worker × 4 threads) | 0,30 GB de RAM, ~0,015 vCPU | 3,30 |
| PostgreSQL 18 | 0,20 GB de RAM, ~0,01 vCPU, 1 GB de disco | 2,35 |
| Cron (96 execuções por dia, ~20 s cada) | liga, roda e desliga | 0,30 |
| Saída de dados | ~1 GB | 0,05 |
| Ambiente de testes (Serverless: dorme sem tráfego) | só quando alguém usa | ~1,00 |
| **Uso estimado** | | **~7,00** |

Tarifas do Railway: vCPU ≈ US$ 20/mês, RAM ≈ US$ 10 por GB-mês, disco US$ 0,15 por GB-mês, saída
US$ 0,05 por GB.

O consumo fica baixo por decisões do próprio sistema:
- páginas renderizadas no servidor;
- um processo só, sem Redis nem Celery (as rotinas vão pelo serviço Cron);
- `statement_timeout` de 30 s;
- testes que reprovam consultas N+1.

Depois do go-live, o painel **Usage** do Railway mostra o consumo real: refaça a tabela no primeiro
mês.

## 5. No radar: mudança de código

| Mudança | Hoje | Recomendado | Economia/mês | O que exige |
|---|---|---|---|---|
| Código de acesso por SMS | Zenvia, pacote mínimo de R$ 100/mês (até 1.000 SMS, quase todos sem uso) | SMS pré-pago (R$ 7,20/mês) | **≈ R$ 92,80** | Adaptador para o provedor pré-pago no módulo `utils/sms` (o envio da Zenvia está isolado ali) |
| E-mail | Nenhum provedor configurado | Brevo Free | — | Só configuração, na implantação: `EMAIL_BACKEND` do Brevo e a chave `BREVO_API_KEY` |
| Código de acesso pelo WhatsApp (opcional, depois) | SMS | Template de autenticação (R$ 0,035) com SMS só de reserva | ≈ R$ 4,60 | Canal novo no OTP e template aprovado na Meta. Com 60 atendimentos a economia é pequena |

A pendência está registrada em [`ARCHITECTURE.md`](ARCHITECTURE.md) (§6.2). O custo da seção 3 já
considera a troca do SMS.

## 6. Alternativas avaliadas e descartadas

| Alternativa | Economia/mês | Por que não |
|---|---|---|
| VPS própria (ex.: Hetzner CX23, ~€ 5,49/mês) com Docker | pequena (custo parecido com o Railway) | A clínica passaria a cuidar de sistema operacional, certificado, backup e monitoramento |
| Nuvem gratuita (Oracle Always Free) ou banco grátis externo (Neon) | até ~R$ 37 | Sem garantia de serviço; banco fora da rede do app, com latência e demora para "acordar". Risco alto para dado de saúde |
| Plano gratuito do Railway (US$ 1 de crédito por mês) | — | O consumo estimado (~US$ 7) não cabe no crédito |
| Amazon SES para e-mail | R$ 0 | Com este volume o Brevo Free já não custa nada; o SES pede conta na AWS |
| Lembrete D-1 só por e-mail | ~R$ 2,63 | O WhatsApp evita mais faltas, e uma falta evitada vale mais que o mês inteiro do sistema |
| Plano Pro do Railway (US$ 20/mês) | — | Só faz sentido com mais de uma pessoa na conta ou para ter suporte |

## 7. Implantação (uma vez)

| Item | Valor |
|---|---|
| Domínio `.com.br`, 1º ano (Registro.br) | R$ 40,00 |
| Chip pré-pago para o número do WhatsApp da API (estimativa). O número não pode estar em uso no aplicativo WhatsApp comum | R$ 20,00 |
| Primeiro crédito de SMS pré-pago (300 SMS × R$ 0,12; não expira) | R$ 36,00 |
| Contas no Railway, Brevo, Meta, Cloudflare, Sentry e healthchecks.io (a Meta exige CNPJ; o Railway pede cartão) | R$ 0,00 |
| **Total** | **R$ 96,00** |

Sem a taxa de ativação de R$ 649 da Zenvia: ela só existe para WhatsApp/RCS pela Zenvia, e o sistema
fala com a Meta direto.

## 8. Travas de custo no sistema

Cada canal pago tem um teto no código. Abuso ou erro não vira fatura:

| Risco | Trava | Onde |
|---|---|---|
| Robô pedindo SMS em massa (*SMS pumping*) | Turnstile + 3 SMS/h por telefone, 10/h por IP e **60/h no site todo**: o pior caso fica em ~R$ 7/h e para no teto | `utils/sms`, env `SMS_MAX_*` |
| Reenvio de código em sequência | 60 s entre reenvios; código vale 10 min | `OTP_REENVIO_MINIMO_SEG` |
| Spam pelo formulário de contato | 5/min e 20/dia por IP; teto de 50/dia no site | `views/publico/contato` |
| NPS repetido | no máximo 1 pesquisa a cada 90 dias por cliente, nunca em retorno | `constants.NPS_INTERVALO_DIAS` |
| Marketing caro por WhatsApp | promoção só por e-mail; WhatsApp só em template de utilidade | `services/comunicacao`, `services/marketing` |
| Lembrete para quem não aceitou WhatsApp | vai por e-mail (custo zero) | `services/lembretes` |
| Envio sem provedor configurado | falha fechada: nada sai, nada é cobrado nem marcado como enviado | `utils/sms`, `utils/whatsapp` |
| Avisos à equipe | Web Push gratuito em vez de SMS ou WhatsApp | `services/push` |

## 9. Esforço de operação (tempo, não dinheiro)

| Frequência | Tarefa | Tempo |
|---|---|---|
| Semanal | Olhar o Sentry e o healthchecks (rotina atrasada, erro novo) | ~15 min |
| Mensal | Testar a restauração do backup (`scripts/backup/testar_restauracao.sh`) | ~30 min |
| Mensal | Conferir as faturas (Railway, Meta) e o saldo de SMS | ~15 min |
| Anual | Renovar o domínio e revisar este documento e a planilha com os preços do ano | ~1 h |
| Quando mudar o texto | Reaprovar o template do WhatsApp na Meta | ~1 dia de espera |

## 10. Fontes (consultadas em 30/10/2026)

- Railway, planos e tarifas: https://railway.com/pricing
- Railway Serverless: https://docs.railway.com/reference/app-sleeping
- Registro.br, valor de R$ 40/ano: https://www.nuvemshop.com.br/blog/registro-br
- SMS pré-pago sem mensalidade: https://smsgo.com.br/
- SMS para OTP no Brasil (comparativo de provedores): https://x-apps.com.br/sms-verificacao-otp-brasil-provedores-precos/
- Zenvia, pacotes de SMS: https://zenvia.com/zoe/
- Meta, modelo de cobrança do WhatsApp Business Platform: https://developers.facebook.com/docs/whatsapp/pricing
- Tarifas do WhatsApp no Brasil, em vigor desde 01/10/2026 (tabela da Meta reproduzida por terceiro):
  https://zynfo.ai/whatsapp-pricing/brazil
- WhatsApp, tarifa de autenticação igual à de utilidade: https://www.authgear.com/post/whatsapp-api-pricing
- Brevo, plano gratuito de 300 e-mails/dia (resenha de terceiro): https://www.sender.net/reviews/brevo/pricing/free-plan
- Resend, planos: https://resend.com/pricing
- Cloudflare R2, cota gratuita: https://developers.cloudflare.com/r2/pricing
- Sentry, plano Developer gratuito: https://costbench.com/software/developer-tools/sentry/free-plan/
- healthchecks.io, planos: https://healthchecks.io/pricing/
- Amazon SES: https://www.usecarly.com/blog/amazon-ses-pricing/
- Hetzner, reajustes de 2026 (CX23): https://privatedevops.com/news/hetzner-june-2026-cloud-price-increase-what-to-do
- Câmbio (PTAX de 16/10/2026): https://www.simianx.ai/pt/stories/us-dollar-exchange-rate-by-year-every-year-1971-2026-table
- Cotação oficial: https://www.bcb.gov.br/estabilidadefinanceira/historicocotacoes

Algumas tarifas vieram de páginas de terceiros. Antes de contratar, confira o valor na página do
próprio provedor.
