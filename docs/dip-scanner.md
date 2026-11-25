# Dip Scanner

Escaneia um universo fechado de ações americanas à procura de **quedas fortes por causas pontuais** (não estruturais), para swing de 3 a 10 dias.

## Como funciona

1. Baixa 1 ano de OHLCV diário (yfinance) do universo e dos ETFs de referência (SPY, QQQ, SMH, IGV, XLV, XLF, IBIT e qualquer outro indicado em `DIP_UNIVERSE`). O pregão de referência é a última data comum à maioria dos tickers; tickers sem a barra desse pregão são ignorados e listados na saída, e um ETF desatualizado não é usado na comparação.
2. **Gatilho**: queda ≥ `DIP_MIN_DROP_PCT`% ou ≥ `DIP_MIN_ATR_MULT`× ATR(14), com volume ≥ `DIP_MIN_VOLUME_RATIO`× a média de 20 dias. Também dispara com uma queda acumulada ≥ `DIP_MULTIDAY_DROP_PCT`% em 3 sessões. Um ticker já sinalizado nas últimas `DIP_COOLDOWN_SESSIONS` sessões não volta a gerar sinal, para o mesmo evento não contar duas vezes.
3. **Queda relativa ajustada ao beta** (`abn_1d`): retorno do dia menos β × retorno do ETF, com β calculado nas 120 sessões anteriores (limitado a 0–4; sem 60 sessões de histórico usa-se a diferença simples `excess_1d`). Assim, COIN caindo 6% com o ETF em −2% e β≈3 é tratado como movimento de mercado, não como queda própria.
4. **Ordenação**: os sinais são ordenados pela intensidade da queda em unidades de ATR (a queda de 3 sessões é dividida por √3) e cortados em `DIP_MAX_SIGNALS`. Uma queda de 6% numa ação calma passa à frente de 9% numa ação volátil.
5. **Contexto de mercado**: se o SPY cair ≥ `DIP_MARKET_DAY_PCT`%, a saída e a página indicam "dia de mercado" (só informativo: no backtest, estes sinais não foram piores). Indicam também o regime: SPY acima da MM200 (alta) ou abaixo (correção).
6. **Técnica** (pontuação 0–100, base 50). Só entram regras cujo efeito no backtest teve o mesmo sinal em 2012–2021 e em 2022–2026:

   | Regra | Pontos |
   | --- | --- |
   | Mercado em correção (SPY abaixo da MM200) | +15 |
   | RSI(14) entre 25 e 35 | +10 |
   | Fechamento na metade superior do range do dia | +5 |
   | Fechamento nos 10% inferiores do range | −10 |
   | Volume ≥ 3× a média (notícia forte, risco de continuação) | −8 |
   | Gap de abertura ≤ −3% | −5 |

   A posição da ação vs MM200, o RSI abaixo de 25 e a queda relativa ao setor (`abn_1d`) mudaram de sinal entre períodos e não pontuam; continuam na saída e são considerados na análise.
7. **Resultados (earnings)**: para cada sinal consulta as datas de resultados no yfinance. Um anúncio às 16:00 ET ou depois reage no pregão seguinte; antes disso, no próprio dia. Assinala reação a resultados nas últimas 3 sessões (com a surpresa de EPS) e próximos resultados nos 14 dias seguintes, que são risco para o trade. Se a consulta falhar, o scan continua e indica "indisponível".
8. **Níveis** calculados de forma determinística: entrada entre o fechamento − 0,5×ATR e o fechamento; stop na mínima de 5 dias − 1×ATR (no backtest, 0,5×ATR foi a pior margem nos dois períodos); alvo na recuperação de 50% da queda desde a máxima de 10 dias.
9. **Saída em JSON** (`python -m radar.scanner scan`): sinais, métricas, níveis, contexto de resultados, a tabela do universo, os resultados atualizados dos sinais anteriores e as estatísticas.
10. **Análise do Claude** (rotina diária, ver abaixo): pesquisa as notícias de cada sinal, classifica a causa como `pontual`, `estrutural` ou `incerto`, dá um veredito (`entrar`, `aguardar_estabilizacao` ou `evitar`) com gatilho de entrada objetivo, catalisadores e riscos, e escreve o resumo do pregão.
11. **Registro e página**: tudo é gravado no banco do artefato "Radar do Pregão", que mostra o último pregão, os últimos 15 pregões e o placar.

## Registro de sinais

Cada sinal salva as métricas do dia (`abn_1d`, `beta`, `gap_pct`, `intensity`, `market_day`, `earnings_reaction`, `next_earnings`), os níveis, a análise (`cause_type`, `verdict`, `cause_summary`, `news`...) e quem analisou (`ai_model`: `claude`, ou ausente nos sinais antigos do Gemini). Em cada execução, `outcomes` é preenchido com:

| Campo | Significado |
| --- | --- |
| `basis` | `next_open`: tudo é medido a partir da abertura da sessão seguinte ao sinal, porque o scan roda depois do fechamento |
| `entry_open` | Abertura da sessão seguinte (preço de entrada assumido) |
| `limit_filled` | Se a mínima dessa sessão tocou `entry_low` (a ordem limitada teria sido executada) |
| `ret_3d` / `ret_5d` / `ret_10d` | Retorno do fechamento de N sessões depois em relação a `entry_open` |
| `excess_3d` / `excess_5d` / `excess_10d` | O mesmo retorno menos o do SPY no mesmo período |
| `exit` | `alvo`, `stop`, `ambos` (os dois no mesmo dia; conta como stop), `tempo` (10 sessões) ou `sem_entrada` (a abertura de entrada já estava abaixo do stop ou acima do alvo) |
| `exit_ret` | Retorno simulado do trade; um gap através do stop ou do alvo sai na abertura |

Registros criados por versões anteriores (medidos a partir do fechamento) são recalculados automaticamente enquanto o pregão do sinal estiver no histórico de 1 ano.

O placar agrega as estatísticas para todos os sinais, por causa, por veredito, por regime de mercado e por analista: média a 5 sessões, média em relação ao SPY, % de positivos, % de alvos antes do stop e trade médio. A comparação de "Entrar" com "Todos" mostra se a análise acrescenta valor. Sinais em que a análise falhou (`ai_failed`) contam em "Todos" e no regime, mas não nos grupos da análise. Com poucos sinais por semana, são precisos meses de registro antes de tirar conclusões.

## Backtest

`radar/backtest.py` aplica a anos de histórico as mesmas funções do scanner (métricas, gatilho, pausa por ticker, ordenação e limite diário, pontuação, níveis e o `update_outcomes` do registro). Não testa a análise de notícias, porque as notícias atuais sobre o passado trariam informação do futuro; o resultado corresponde ao grupo "Todos" do registro.

```bash
python -m radar.backtest --period 15y --universe extended --split 2022-01-01
python -m radar.backtest --stop-buffers 0.5,1,1.5,2 --cost-pct 0.1 --csv sinais.csv
```

| Opção | Default | Descrição |
| --- | --- | --- |
| `--period` | `10y` | Histórico do yfinance (`15y`, `max`, ...) |
| `--universe` | `default` | `extended` junta ~30 ações que caíram muito (Intel, Zoom, Peloton, Moderna...) para reduzir o viés de sobrevivência |
| `--tickers` | — | Substitui o universo (`NVDA:SMH,TTD`) |
| `--split` | — | Data para comparar os sinais antes e depois |
| `--stop-buffers` | `0.5,1,1.5` | Margens do stop a comparar, em ATR, também em múltiplos de R (resultado / risco planeado) |
| `--cost-pct` | `0.1` | Custo por trade (ida e volta), em % |
| `--csv` | — | Grava todos os sinais |

O relatório mostra a comparação com um dia qualquer nas mesmas ações (baseline) e os resultados por ano, regime do SPY, dia de mercado, `abn_1d`, posição vs MM200, pontuação, cada regra técnica (com / sem) e margem do stop.

Limitações: o yfinance só tem ações que ainda negoceiam (o universo `extended` reduz o viés de sobrevivência, mas não o elimina); sem slippage; sinais do mesmo dia estão correlacionados, por isso o erro-padrão é otimista; os pesos da pontuação foram escolhidos com este histórico, por isso o backtest da pontuação não é uma validação independente; o registro em produção é.

Para mudar uma regra: rodar o backtest antes e depois com `--split`, e só manter a mudança se melhorar nos dois períodos.

## Rotina diária

Uma tarefa agendada do Claude roda de segunda a sexta às 12:10 (Lisboa), na manhã seguinte ao pregão e antes da abertura de Nova York (14:30 em Lisboa), e segue o roteiro fixo em `.claude/skills/radar-diario/SKILL.md`:

1. Lê o banco do artefato (sinais, resultados, pregões já gravados e `meta/state`).
2. `python -m radar.store journal` monta o registro e `python -m radar.scanner scan` roda o scanner.
3. Pesquisa as notícias de cada sinal, incluindo as da noite e do pré-mercado (`morning_note`), e escreve `analysis.json` (causa, veredito, gatilho, riscos, fontes e o resumo com o que vigiar no dia).
4. `python -m radar.store prepare` valida a análise e gera o plano de gravação, que é aplicado com o ArtifactData.
5. A última mensagem da sessão é o resumo curto que chega como notificação no celular.

Na segunda, a rotina analisa o pregão de sexta. Depois de um feriado nos EUA, o pregão já está gravado e a rotina só atualiza os resultados.

Para rodar fora do horário, peça ao Claude: "rode o radar de hoje".

### Banco do artefato

| Documento | Conteúdo | Escrita |
| --- | --- | --- |
| `signals/<data>_<TICKER>` | Métricas, níveis e análise do sinal | Criado uma vez |
| `days/<data>` | Mercado do dia e resumo | Criado uma vez |
| `outcomes/<data>_<TICKER>` | Resultado final (10 sessões e saída definidas) | Criado quando o sinal fecha |
| `meta/state` | Resultados em aberto, estatísticas e status da última execução | Reescrito a cada execução (com versão) |

## Execução local

```bash
pip install -r requirements-dev.txt
python -m radar.scanner scan --out out/day.json                      # sem registro anterior
python -m radar.scanner scan --journal registro.json --out out/day.json --tickers NVDA:SMH,TTD
python -m radar.scanner stats --journal registro.json
python -m pytest -q
```

`.github/workflows/ci.yml` roda os testes em cada PR e em cada push na `main`.

## Configuração

Variáveis opcionais (no ambiente ou num `.env`):

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `DIP_UNIVERSE` | 40 ações internas | `TICKER[:ETF]` separados por vírgula |
| `DIP_MIN_DROP_PCT` | 5 | Queda diária mínima (%) |
| `DIP_MIN_ATR_MULT` | 2.0 | Ou queda ≥ N× ATR(14) |
| `DIP_MIN_VOLUME_RATIO` | 1.3 | Volume vs média de 20d |
| `DIP_MULTIDAY_DROP_PCT` | 10 | Queda em 3 sessões (dispensa o filtro de volume) |
| `DIP_MAX_SIGNALS` | 8 | Máximo de sinais analisados por execução |
| `DIP_COOLDOWN_SESSIONS` | 5 | Sessões sem novo sinal do mesmo ticker depois de um sinal (0 desliga) |
| `DIP_MARKET_DAY_PCT` | 2.5 | Queda do SPY (%) a partir da qual o dia é marcado como dia de mercado |

Ferramenta de apoio à decisão. Não é recomendação de investimento.
