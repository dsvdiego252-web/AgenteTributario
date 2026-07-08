# Agente Tributário

Portal estático (HTML/CSS/JS) com duas abas principais:

- **Reforma Tributária** — novidades sobre a Reforma Tributária (IBS, CBS, Imposto Seletivo, Comitê Gestor do IBS), reunidas de fontes oficiais e da imprensa especializada.
- **ICMS**
  - **Novidades** — repercussão na imprensa sobre Portarias SRE (SEFAZ-SP) do ICMS.
  - **Base Legal** — registro oficial completo das Portarias SRE, extraído diretamente da listagem da SEFAZ-SP.

O conteúdo é lido de `data/reforma_tributaria.json`, `data/icms_sre.json` e
`data/icms_base_legal.json`. Esses arquivos são atualizados automaticamente
todos os dias por um GitHub Action (`.github/workflows/update-data.yml`), que
roda `scripts/update_data.py`, faz commit das novidades e assim o site já
nasce atualizado quando alguém acessa.

## Estrutura

```
index.html                        portal (abas Reforma Tributária / ICMS > Novidades, Base Legal)
assets/style.css                   estilos
assets/app.js                      carrega os JSON e controla abas/busca
data/reforma_tributaria.json       novidades da reforma tributária
data/icms_sre.json                 portarias SRE (ICMS) — repercussão/notícias
data/icms_base_legal.json          portarias SRE (ICMS) — registro legal oficial completo
scripts/update_data.py             coleta diária (Python 3, só biblioteca padrão)
.github/workflows/update-data.yml  agenda diária (cron) + commit automático
```

## Como funciona a coleta diária

`scripts/update_data.py`:

1. **Reforma Tributária**: consulta o Google News RSS com buscas separadas para
   fontes oficiais (gov.br, Comitê Gestor do IBS, Receita Federal, Congresso) e
   para a imprensa especializada em tributos.
2. **ICMS → Novidades**: combina as portarias do ano corrente extraídas da
   listagem oficial com uma busca no Google News RSS por matérias de portais
   especializados (LegisWeb, Contábeis, etc.) citando o número da Portaria SRE.
3. **ICMS → Base Legal**: raspa diretamente a listagem oficial da SEFAZ-SP em
   `legislacao.fazenda.sp.gov.br/Paginas/Atos.aspx?Tipo=Portarias%20CAT/SRE`,
   uma página por ano. Na primeira execução (arquivo vazio) percorre todo o
   histórico disponível (2011 em diante); nas execuções seguintes checa apenas
   o ano corrente e o anterior — suficiente para pegar as novas portarias
   assim que são publicadas. A data exata (dia/mês) de cada portaria é obtida
   visitando a página de detalhe, em lotes pequenos por execução (para não
   sobrecarregar o site oficial), com autopreenchimento gradual ao longo dos
   dias até que todo o histórico tenha data precisa.
4. Os itens novos são mesclados aos já existentes (sem duplicar, por link ou
   por número da portaria) e ordenados por data, mantendo os mais recentes.

O script é incremental e idempotente: pode ser rodado manualmente quantas
vezes quiser (`python3 scripts/update_data.py`) sem perder dados já coletados.

> Nota: o ambiente onde este portal foi criado tem acesso de rede restrito, então
> não foi possível validar ao vivo o scraping de `legislacao.fazenda.sp.gov.br` a
> partir daqui. O GitHub Actions roda em uma rede sem essa restrição — e já
> validamos que funciona: a primeira execução automática em produção coletou
> novidades reais direto do site oficial.

## Publicar no GitHub Pages

1. Faça merge desta branch na branch padrão do repositório (ex.: `main`) — o
   agendamento (`schedule`) do GitHub Actions só é lido a partir da branch
   padrão.
2. Em **Settings → Pages**, defina "Source" = "Deploy from a branch", branch
   `main`, pasta `/ (root)`.
3. Pronto: o site fica disponível em `https://<usuario>.github.io/<repo>/` e é
   atualizado automaticamente a cada novo commit gerado pelo Action diário.

## Rodar localmente

```bash
python3 -m http.server 8000
# abrir http://localhost:8000
```

## Rodar a coleta manualmente

```bash
python3 scripts/update_data.py
```
