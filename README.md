# stockmarketradar

Radar de oportunidades em ações americanas: escaneia o mercado depois do fechamento, cruza análise técnica, volume e notícias, pede uma leitura à IA e manda um resumo curto no Telegram.

## O que roda hoje

**Dip Scanner**: procura quedas fortes com causa pontual (não estrutural) para swing de 3 a 10 dias, com níveis de entrada, stop e alvo, registro dos sinais e backtest. Detalhes em [docs/dip-scanner.md](docs/dip-scanner.md).

Relatório: https://rafaelalvescosta-commits.github.io/stockmarketradar/

## Começar

1. Cadastre os secrets em Settings → Secrets and variables → Actions (lista em [docs/dip-scanner.md](docs/dip-scanner.md#secrets-e-variáveis)).
2. Actions → Dip Scanner → Run workflow.
3. Settings → Pages → *Deploy from a branch* → `gh-pages` / `(root)`.

Localmente:

```bash
pip install -r requirements-dev.txt
python -m radar.scanner --dry-run
python -m pytest -q
```

Ferramenta de apoio à decisão. Não é recomendação de investimento.
