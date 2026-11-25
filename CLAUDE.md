# Regras do repositório

- Projeto pessoal: radar de oportunidades em ações dos EUA (swing de 3 a 10 dias). Textos para o usuário em português do Brasil.
- Código em `radar/` (`scanner.py`: parte numérica; `store.py`: banco do artefato; `backtest.py`), testes em `tests/`, especificação em `docs/dip-scanner.md`, rotina diária em `.claude/skills/radar-diario/SKILL.md`, página em `artifact/radar.html`.
- Atualize a documentação quando mudar comportamento, configuração ou o formato do banco, e o `.env.example` quando criar variável.
- Antes de entregar: `python -m pytest -q`. Testes não acessam a rede (simule yfinance).
- Mudança de regra de sinal ou pontuação: rode o backtest antes e depois com `--split` e só mantenha se melhorar nos dois períodos.
- Números (gatilhos, níveis, resultados, estatísticas) vêm do código; a análise nunca inventa nem altera números. Análise que falhou aparece como falha (`ai_failed`), nunca como uma classificação.
- Banco do artefato: `signals`, `days` e `outcomes` são criados uma vez e não são reescritos; só `meta/state` é reescrito. O histórico é real: não apague registros.
- Não faça commit, push ou PR sem o usuário pedir.
