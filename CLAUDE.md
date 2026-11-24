# Regras do repositório

- Projeto pessoal: radar de oportunidades em ações americanas (swing de 3 a 10 dias). Textos para o usuário em português do Brasil.
- Código em `radar/`, testes em `tests/`, especificação em `docs/dip-scanner.md`. Atualize a documentação quando mudar comportamento, configuração ou secrets, e o `.env.example` quando criar variável.
- Antes de entregar: `python -m pytest -q`. Testes não acessam a rede (simule yfinance, LLM, notícias e Telegram).
- Mudança de regra de sinal ou pontuação: rode o backtest antes e depois com `--split` e só mantenha se melhorar nos dois períodos.
- Falha de notícias, IA ou Telegram nunca deve derrubar o scan. Falha de IA aparece como "IA falhou", nunca como uma classificação.
- Não registre segredos em log. Não faça commit, push ou PR sem o usuário pedir.
- O registro de sinais (`gh-pages: data/signals.json`) é histórico real: não apague nem reescreva campos existentes; acrescente campos novos.
