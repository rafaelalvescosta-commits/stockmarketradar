---
name: radar-diario
description: Rotina diária do Radar do Pregão. Roda o Dip Scanner no último pregão dos EUA, pesquisa as notícias de cada sinal, classifica a causa da queda, grava tudo no artefato "Radar do Pregão" e termina com um resumo curto. Use quando a tarefa agendada disparar ou quando o usuário pedir "rode o radar de hoje".
---

# Radar diário

Artefato: https://claude.ai/artifact/BxBAauzTjPdS83K8MVVqVe (página "Radar do Pregão", banco de dados próprio).
Usuário: trader de swing (3 a 10 dias) em ações dos EUA. Escreva tudo em **português do Brasil**, direto e sem jargão desnecessário.

A parte numérica (gatilhos, níveis, resultados, estatísticas) é do código. **Nunca invente ou altere números**: sua parte é notícia, causa, veredito e texto.

## 0. Preparar

`$W` abaixo é uma pasta de trabalho: use `<sua pasta de rascunho (scratchpad)>/radar`, sempre com caminho absoluto (inclusive no `out_dir` do ArtifactData).

```bash
W=<scratchpad>/radar && mkdir -p $W
[ -d /home/user/stockmarketradar ] || git clone --depth 1 https://github.com/rafaelalvescosta-commits/stockmarketradar /home/user/stockmarketradar
cd /home/user/stockmarketradar && git pull --ff-only -q || true
python -m pip install -q -r requirements.txt   # "python -m pip" garante o mesmo Python que roda o scanner
```

Carregue a ferramenta do banco: ToolSearch `select:ArtifactData`.

## 1. Ler o banco

Com `ArtifactData` (url do artefato acima):

1. `list` da coleção `signals`, `query.limit` 1000, `out_dir` = `$W/db`.
2. `list` da coleção `outcomes`, mesmos parâmetros.
3. `list` da coleção `days`, mesmos parâmetros.
4. `get` de `meta` / `state` com `out_dir` = `$W/db`. **Anote a `version`** que o resultado mostra (se o documento não existir, não há versão).

Se alguma lista vier com `next_cursor`, continue com `query.cursor` até o fim.

```bash
cd /home/user/stockmarketradar
python -m radar.store journal --db-dir $W/db --out $W/journal.json
```

## 2. Rodar o scanner

```bash
python -m radar.scanner scan --journal $W/journal.json --out $W/day.json
```

- Código de saída 1 (sem dados de mercado): tente mais uma vez em 2 minutos. Se falhar de novo, pule para o passo 6 e registre a falha.
- Leia `$W/day.json`: `date`, `market_text`, `signals` (cada um com `record`, `metrics`, `earnings_text`, `tech_notes`, `levels`), `stats`.
- Se `date` já estiver em `$W/db/days/` (feriado ou execução repetida), não analise sinais: vá direto ao passo 5 com `{"signals": {}}` (só os resultados serão atualizados).

## 3. Analisar cada sinal

Para cada sinal (no máximo 8), use WebSearch para descobrir **por que a ação caiu** no pregão `date`, com 2 ou 3 buscas por ação, por exemplo `"<Empresa> stock falls <mês dia>"`, `"<TICKER> shares drop reason"` e, se houver resultados recentes (`earnings_text`), `"<Empresa> earnings guidance"`. Prefira fontes primárias e agências (Reuters, Bloomberg, CNBC, WSJ, Barron's, MarketWatch, comunicados da empresa). Abra a matéria principal quando o título não bastar.

Classifique com este critério:

- **pontual**: venda generalizada, contágio de setor, downgrade, resultado um pouco abaixo, ruído macro, notícia mal interpretada, venda forçada ou rebalanceamento.
- **estrutural**: perda de cliente relevante, guidance muito cortado, mudança na tese de crescimento, problema regulatório, contábil ou legal sério, diluição relevante, disrupção competitiva.
- **incerto**: as notícias não explicam a queda ou são contraditórias.

Use `abn_1d` (queda ajustada ao beta em relação ao ETF do setor): perto de 0 significa movimento do setor ou do mercado; muito negativo significa queda própria da ação.

Veredito:

- **entrar**: causa pontual com convicção, sem resultados marcados dentro da janela do trade (`next_earnings`), e sinal técnico de estabilização (fechamento na metade de cima do dia, volume não extremo). Diga exatamente onde entrar dentro da faixa calculada.
- **aguardar_estabilizacao**: causa pontual ou incerta, mas sem estabilização ainda. O `trigger_to_enter` precisa ser **objetivo e verificável no próximo pregão** (ex.: "fechamento acima de 212,40, a máxima de hoje", "não renovar a mínima de 386 por 2 pregões"). Nada de "esperar o mercado acalmar".
- **evitar**: causa estrutural, ou resultados dentro da janela, ou queda ainda em aceleração com notícia forte.

Seja cético. Não invente notícias, números ou citações. Se não encontrar a causa, diga isso e use `incerto`. Considere o placar em `stats` (por exemplo, se "Aguardar" está indo melhor que "Entrar", ou se o regime de correção está derrubando os sinais) e mencione no resumo do dia quando for relevante.

Escreva `$W/analysis.json`:

```json
{
  "ai_model": "claude",
  "summary": "2 a 4 frases: tom do pregão (SPY/QQQ, regime), o que puxou as quedas, o sinal mais interessante e o que vigiar amanhã.",
  "signals": {
    "TICKER": {
      "cause_type": "pontual | estrutural | incerto",
      "confidence": 0,
      "cause_summary": "1 a 2 frases: o que causou a queda.",
      "why_classification": "Por que é pontual ou estrutural.",
      "catalysts": ["o que pode fazer a ação recuperar"],
      "risks": ["o que pode dar errado no trade"],
      "verdict": "entrar | aguardar_estabilizacao | evitar",
      "trigger_to_enter": "condição objetiva para entrar",
      "comment_on_levels": "comentário curto sobre entrada, stop e alvo calculados",
      "news": [{"title": "...", "url": "https://...", "source": "Reuters", "date": "2026-11-24"}]
    }
  }
}
```

`confidence` é um número de 0 a 100. `news` tem de 2 a 5 itens que você realmente usou, só com URLs `https://`. Se não conseguir analisar um sinal (busca falhou), use `{"ai_failed": true, "cause_summary": "motivo"}` em vez de chutar.

Sem sinais no dia: `"signals": {}` e um `summary` curto sobre o pregão.

## 4. Preparar a gravação

```bash
python -m radar.store prepare --day $W/day.json --analysis $W/analysis.json \
  --db-dir $W/db --out-dir $W/out [--state-version N]
```

Passe `--state-version` com a versão anotada no passo 1 se `meta/state` existia. Se o comando listar `ERRO`, corrija `analysis.json` e rode de novo.

## 5. Gravar

Leia `$W/out/writes.json` (lista de lotes). Para cada lote, chame `ArtifactData` `batch` com `writes` = o lote exatamente como está (cada entrada já tem `op`, `collection`, `doc_id`, `file_path` e, em `meta/state`, `if_version`).

Se `meta/state` falhar por versão desatualizada: faça `get` de novo, rode o passo 4 com a versão nova e grave só esse lote.

## 6. Se algo falhou

Grave o status mesmo assim, para a página mostrar o problema: `get` de `meta/state` e `update` com `if_version` e `{"status": {"ok": false, "last_run": "<agora UTC ISO>", "session_date": "<date ou vazio>", "message": "<o que falhou, curto>"}}`. Se o documento não existir, use `set` com esse mesmo conteúdo.

## 7. Terminar

A última mensagem vira a notificação no celular. Escreva só isto (sem tabelas):

```
📉 Radar <dd/mm> · <N> sinal(is)
<TICKER> <queda>: <causa em poucas palavras> → <veredito>
...
<1 frase do resumo do dia>
Página: https://claude.ai/artifact/BxBAauzTjPdS83K8MVVqVe
```

Sem sinais: `📉 Radar <dd/mm> · sem quedas relevantes`, mais a frase do dia e o link.
