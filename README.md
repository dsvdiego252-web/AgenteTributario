# Agente Tributário

Portal estático (HTML/CSS/JS) com três abas principais:

- **Reforma Tributária** — novidades sobre a Reforma Tributária (IBS, CBS, Imposto Seletivo, Comitê Gestor do IBS), reunidas de fontes oficiais e da imprensa especializada.
- **ICMS**
  - **Novidades** — repercussão na imprensa sobre Portarias SRE (SEFAZ-SP) do ICMS.
  - **Base Legal** — registro oficial completo das Portarias SRE, extraído diretamente da legislação da SEFAZ-SP, com filtro por setor/segmento.
- **Consulta Produto** — busca por descrição de produto para localizar o NCM e ver IPI, ICMS (alíquota geral + benefícios de exemplo), Substituição Tributária (CEST/MVA) e PIS/COFINS (monofásico/alíquota zero ou regime geral).

O conteúdo é lido de arquivos JSON em `data/`. Esses arquivos são atualizados
automaticamente por dois GitHub Actions:
- `.github/workflows/update-data.yml` — todos os dias, novidades (Reforma
  Tributária, ICMS Novidades e Base Legal).
- `.github/workflows/update-produto-data.yml` — semanalmente, dados de
  referência de produto (NCM/TIPI, CEST/MVA, PIS/COFINS), que mudam pouco.

## Estrutura

```
index.html                                 portal (abas Reforma Tributária / ICMS / Consulta Produto)
assets/style.css                            estilos
assets/app.js                               Reforma Tributária e ICMS: carrega os JSON e controla abas/busca
assets/produto.js                           Consulta Produto: busca por NCM e monta a tela de resultado
data/reforma_tributaria.json                novidades da reforma tributária
data/icms_sre.json                          portarias SRE (ICMS) — repercussão/notícias
data/icms_base_legal.json                   portarias SRE (ICMS) — registro legal oficial completo
data/ncm_tipi.json                          NCM + descrição (Siscomex) + alíquota de IPI (TIPI)
data/cest_st_sp.json                        CEST/segmento/MVA (base nacional, Convênio ICMS 142/2018) — vigência
                                             em SP é sempre da Portaria CAT 68/2019 e suas alterações
data/pis_cofins_especial.json               NCMs com PIS/COFINS monofásico ou alíquota zero (tabelas SPED)
data/icms_beneficios_sample.json            amostra curada manualmente de isenção/redução de BC (Anexos I/II RICMS-SP)
data/icms_aliquotas_sp.json                 alíquotas internas do ICMS por categoria (RICMS/SP arts. 52 a 56-C), curado
data/icms_alimenticios_sp.json              Anexo XVI da CAT 68/2019 (ST de produtos alimentícios) com histórico de
                                             revogações + IVA-ST vigente (Portaria SRE 12/2026), curado
data/icms_reducoes_alimenticios_sp.json     reduções de BC da Cesta Básica (Art. 3º) e de Produtos Alimentícios
                                             (Art. 39) do Anexo II do RICMS/SP, curado
data/icms_limpeza_sp.json                   Anexo XIII da CAT 68/2019 (ST de produtos de limpeza) + IVA-ST
                                             (Portaria SRE 55/2025) + PMPF da água sanitária (Portaria SRE
                                             57/2025) — todo o anexo e as duas portarias são revogados a partir
                                             de 01/08/2026 pela Portaria SRE-20/26, curado
scripts/update_data.py                      coleta diária (Python 3, só biblioteca padrão)
scripts/update_produto_data.py              coleta semanal de dados de produto (precisa de openpyxl)
scripts/requirements.txt                    dependências Python (openpyxl)
.github/workflows/update-data.yml           agenda diária (cron) + commit automático
.github/workflows/update-produto-data.yml   agenda semanal (cron) + commit automático
```

## Como funciona a coleta diária (notícias)

`scripts/update_data.py`:

1. **Reforma Tributária**: consulta o Google News RSS com buscas separadas para
   fontes oficiais (gov.br, Comitê Gestor do IBS, Receita Federal, Congresso) e
   para a imprensa especializada em tributos.
2. **ICMS → Novidades**: combina as portarias do ano corrente extraídas da
   listagem oficial com uma busca no Google News RSS por matérias de portais
   especializados (LegisWeb, Contábeis, etc.) citando o número da Portaria SRE.
3. **ICMS → Base Legal**: raspa diretamente a listagem oficial da SEFAZ-SP em
   `legislacao.fazenda.sp.gov.br/Paginas/Atos.aspx?Tipo=Portarias%20CAT/SRE`,
   uma página por ano. Cada portaria já vem com título, ementa e data exata de
   publicação embutidos na própria listagem. Na primeira execução (ou se o
   backfill anterior não completou com sucesso) percorre todo o histórico
   disponível (2011 em diante); depois disso, checa apenas o ano corrente e o
   anterior — suficiente para pegar as novas portarias assim que são
   publicadas.
4. Os itens novos são mesclados aos já existentes (sem duplicar, por link ou
   por número da portaria, com desempate por número quando a data é igual) e
   ordenados da mais recente para a mais antiga.

O script é incremental e idempotente: pode ser rodado manualmente quantas
vezes quiser (`python3 scripts/update_data.py`) sem perder dados já coletados.

## Consulta Produto — o que é automático e o que é amostra

A aba **Consulta Produto** combina fontes de qualidades bem diferentes — é
importante saber qual é qual antes de confiar no resultado:

| Dado | Fonte | Como é obtido |
|---|---|---|
| NCM + descrição | Portal Único Siscomex | API pública, automática, cobertura completa |
| Alíquota de IPI | TIPI (Receita Federal) | Planilha oficial, automática, cobertura completa |
| CEST / NCM (segmento de ST) | Convênio ICMS 142/2018 (CONFAZ, nacional) | Raspagem automática. **É só a base nacional (o que pode ter ST) — quem decide o que efetivamente tem ICMS-ST em SP e o MVA é a Portaria CAT 68/2019 e suas alterações.** Um item pode constar aqui e já ter sido revogado da ST em SP por uma alteração posterior da CAT 68/2019; a tela sempre avisa disso e recomenda confirmar na CAT 68/2019 vigente |
| PIS/COFINS monofásico / alíquota zero | Tabelas 4.3.10/4.3.13 do SPED Contribuições | Automática |
| PIS/COFINS regime geral (Lucro Real/Presumido) | Lei 10.637/02, Lei 10.833/03, Lei 9.718/98 | Alíquotas fixas (1,65%/7,60% e 0,65%/3,00%), não variam por produto |
| **Isenção / redução de base de cálculo do ICMS (Anexos I e II do RICMS/SP)** | `data/icms_beneficios_sample.json` | **Amostra pequena, curada manualmente** — os anexos são texto legal, não uma tabela "NCM → benefício"; exigem interpretação jurídica |
| **Alíquota interna do ICMS por categoria** | `data/icms_aliquotas_sp.json` | **Curado manualmente** a partir do texto oficial dos artigos 52 a 56-C do RICMS/SP — cobre as alíquotas diferenciadas mais comuns (7%/12%/20%/25%/30% + adicional FECOEP), não todas as exceções pontuais |
| **ST de Produtos Alimentícios (Anexo XVI da CAT 68/2019) + IVA-ST** | `data/icms_alimenticios_sp.json` | **Curado manualmente** a partir do texto oficial da CAT 68/2019 (com histórico de revogações, incluindo a revogação em massa da Portaria SRE 64/2025) e da Portaria SRE 12/2026 (IVA-ST vigente) |
| **Reduções de BC de Cesta Básica (Art. 3º) e Produtos Alimentícios (Art. 39) do Anexo II** | `data/icms_reducoes_alimenticios_sp.json` | **Curado manualmente** a partir do texto oficial do RICMS/SP |
| **ST de Produtos de Limpeza (Anexo XIII da CAT 68/2019) + IVA-ST/PMPF** | `data/icms_limpeza_sp.json` | **Curado manualmente** a partir do texto oficial da CAT 68/2019, da Portaria SRE 55/2025 (IVA-ST) e da Portaria SRE 57/2025 (PMPF da água sanitária) — todo o anexo e as duas portarias são revogados a partir de 01/08/2026 pela Portaria SRE-20/26, o que a tela já avisa |

**A consulta é automática e não substitui a leitura do texto oficial nem a
orientação de um profissional.** Isso vale especialmente para os dois últimos
itens da tabela.

`scripts/update_produto_data.py` foi escrito sem poder validar ao vivo os
formatos reais das páginas de CEST/NCM (Convênio 142/2018) e das tabelas do
SPED — o acesso a essas fontes estava bloqueado no ambiente onde o script foi
criado. Por isso ele tem logs de diagnóstico verbosos (`[debug]`), para que,
se o parser não bater com o formato real numa execução em produção, dê para
corrigir rapidamente lendo os logs do Action — foi assim que NCM/TIPI e a
Base Legal também evoluíram (cada uma precisou de 1-2 rodadas de ajuste após
ver o formato real).

## Publicar no GitHub Pages

1. Faça merge desta branch na branch padrão do repositório (ex.: `main`) — o
   agendamento (`schedule`) do GitHub Actions só é lido a partir da branch
   padrão.
2. Em **Settings → Pages**, defina "Source" = "Deploy from a branch", branch
   `main`, pasta `/ (root)`.
3. Pronto: o site fica disponível em `https://<usuario>.github.io/<repo>/` e é
   atualizado automaticamente a cada novo commit gerado pelos Actions.

## Rodar localmente

```bash
python3 -m http.server 8000
# abrir http://localhost:8000
```

## Rodar a coleta manualmente

```bash
python3 scripts/update_data.py
pip install -r scripts/requirements.txt && python3 scripts/update_produto_data.py
```
